"""
Pure-Python QR code generator producing SVG + PNG output.
No external qrcode library needed.
"""

GF_EXP = [0] * 512
GF_LOG = [0] * 256

def _init_gf():
    x = 1
    for i in range(255):
        GF_EXP[i] = x
        GF_LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11d
    for i in range(255, 512):
        GF_EXP[i] = GF_EXP[i - 255]

_init_gf()

def gf_mul(a, b):
    if a == 0 or b == 0: return 0
    return GF_EXP[GF_LOG[a] + GF_LOG[b]]

def gf_poly_mul(p, q):
    r = [0] * (len(p) + len(q) - 1)
    for i, pi in enumerate(p):
        for j, qj in enumerate(q):
            r[i+j] ^= gf_mul(pi, qj)
    return r

def gf_poly_div(dividend, divisor):
    msg = list(dividend)
    for i in range(len(dividend) - len(divisor) + 1):
        coef = msg[i]
        if coef != 0:
            for j in range(1, len(divisor)):
                if divisor[j] != 0:
                    msg[i+j] ^= gf_mul(divisor[j], coef)
    sep = len(dividend) - len(divisor) + 1
    return msg[:sep], msg[sep:]

def rs_generator_poly(nsym):
    g = [1]
    for i in range(nsym):
        g = gf_poly_mul(g, [1, GF_EXP[i]])
    return g

def rs_encode(msg, nsym):
    gen = rs_generator_poly(nsym)
    padded = msg + [0] * nsym
    _, remainder = gf_poly_div(padded, gen)
    return msg + remainder

VERSION_TABLE = [
    (1,16,10,1),(2,28,16,1),(3,44,26,1),(4,64,18,2),(5,86,24,2),
    (6,108,16,4),(7,124,18,4),(8,154,22,4),(9,182,22,5),(10,216,26,5),
]

ALIGN_POS = {
    1:[],2:[6,18],3:[6,22],4:[6,26],5:[6,30],
    6:[6,34],7:[6,22,38],8:[6,24,42],9:[6,26,46],10:[6,28,50],
}

FORMAT_INFO = {
    0:0b101010000010010,1:0b101000100100101,
    2:0b101111001111100,3:0b101101101001011,
    4:0b100010111111001,5:0b100000011001110,
    6:0b100111110010111,7:0b100101010100000,
}

def get_version_for(data_len):
    for ver,cap,ec,blk in VERSION_TABLE:
        if data_len <= cap-4: return ver,cap,ec,blk
    raise ValueError("Data too long")

def encode_data(text):
    data = text.encode('utf-8')
    bits = []
    def add_bits(val,n):
        for i in range(n-1,-1,-1): bits.append((val>>i)&1)
    add_bits(0b0100,4)
    add_bits(len(data),8)
    for b in data: add_bits(b,8)
    return bits

def interleave(blocks):
    result = []
    max_len = max(len(b) for b in blocks)
    for i in range(max_len):
        for b in blocks:
            if i < len(b): result.append(b[i])
    return result

def build_codewords(text):
    ver,cap,ec_per_block,num_blocks = get_version_for(len(text.encode()))
    bits = encode_data(text)
    for _ in range(4): bits.append(0)
    while len(bits)%8: bits.append(0)
    codewords = []
    for i in range(0,len(bits),8):
        byte=0
        for j in range(8): byte=(byte<<1)|bits[i+j]
        codewords.append(byte)
    data_cap = cap - ec_per_block*num_blocks
    pad_bytes=[0xEC,0x11]; pi=0
    while len(codewords)<data_cap:
        codewords.append(pad_bytes[pi%2]); pi+=1
    codewords=codewords[:data_cap]
    block_size=data_cap//num_blocks; remainder=data_cap%num_blocks
    data_blocks=[]; ec_blocks=[]; idx=0
    for i in range(num_blocks):
        sz=block_size+(1 if i<remainder else 0)
        block=codewords[idx:idx+sz]
        data_blocks.append(block)
        ec_blocks.append(rs_encode(block,ec_per_block)[sz:])
        idx+=sz
    final=interleave(data_blocks)+interleave(ec_blocks)
    all_bits=[]
    for byte in final:
        for i in range(7,-1,-1): all_bits.append((byte>>i)&1)
    rem=[0,7,7,7,7,7,0,0,0,0][ver-1]
    all_bits+=[0]*rem
    return all_bits,ver

def make_matrix(size): return [[None]*size for _ in range(size)]

def set_finder(matrix,row,col):
    for r in range(7):
        for c in range(7):
            val=1 if(r==0 or r==6 or c==0 or c==6 or(2<=r<=4 and 2<=c<=4))else 0
            matrix[row+r][col+c]=val

def set_separators(matrix,size):
    for i in range(8):
        for pos in[(7,i),(i,7),(size-8,i),(i,size-8),(7,size-8+i),(size-8+i,7)]:
            r,c=pos
            if 0<=r<size and 0<=c<size and matrix[r][c] is None: matrix[r][c]=0

def set_timing(matrix,size):
    for i in range(8,size-8):
        if matrix[6][i] is None: matrix[6][i]=(i%2==0)
        if matrix[i][6] is None: matrix[i][6]=(i%2==0)

def set_dark_module(matrix,ver): matrix[4*ver+9][8]=1

def set_alignment(matrix,ver):
    positions=ALIGN_POS.get(ver,[])
    for r in positions:
        for c in positions:
            if matrix[r][c] is not None: continue
            for dr in range(-2,3):
                for dc in range(-2,3):
                    rr,cc=r+dr,c+dc
                    val=1 if(dr in(-2,2) or dc in(-2,2) or(dr==0 and dc==0))else 0
                    if matrix[rr][cc] is None: matrix[rr][cc]=val

def reserve_format(matrix,size):
    for pos in[(8,i) for i in range(9) if i!=6]+[(8,size-8+i) for i in range(8)]+\
               [(i,8) for i in range(9) if i!=6]+[(size-7+i,8) for i in range(7)]:
        r,c=pos
        if 0<=r<size and 0<=c<size and matrix[r][c] is None: matrix[r][c]=0

def place_format(matrix,size,mask_id):
    fmt=FORMAT_INFO[mask_id]
    bh=[(8,0),(8,1),(8,2),(8,3),(8,4),(8,5),(8,7),(8,8),(7,8),(5,8),(4,8),(3,8),(2,8),(1,8),(0,8)]
    bv=[(size-1,8),(size-2,8),(size-3,8),(size-4,8),(size-5,8),(size-6,8),(size-7,8),
        (8,size-8),(8,size-7),(8,size-6),(8,size-5),(8,size-4),(8,size-3),(8,size-2),(8,size-1)]
    for i in range(15):
        bit=(fmt>>(14-i))&1
        r,c=bh[i]; matrix[r][c]=bit
        r2,c2=bv[i]; matrix[r2][c2]=bit

def place_data(matrix,size,bits):
    bit_idx=0; col=size-1; going_up=True
    while col>0:
        if col==6: col-=1
        for row_offset in range(size):
            row=(size-1-row_offset) if going_up else row_offset
            for dc in range(2):
                c=col-dc
                if 0<=c<size and matrix[row][c] is None:
                    matrix[row][c]=bits[bit_idx] if bit_idx<len(bits) else 0
                    bit_idx+=1
        col-=2; going_up=not going_up

MASK_FUNCTIONS=[
    lambda r,c:(r+c)%2==0, lambda r,c:r%2==0,
    lambda r,c:c%3==0,     lambda r,c:(r+c)%3==0,
    lambda r,c:(r//2+c//3)%2==0, lambda r,c:(r*c)%2+(r*c)%3==0,
    lambda r,c:((r*c)%2+(r*c)%3)%2==0, lambda r,c:((r+c)%2+(r*c)%3)%2==0,
]

def apply_mask(matrix,size,mask_id):
    fn=MASK_FUNCTIONS[mask_id]
    result=[row[:] for row in matrix]
    for r in range(size):
        for c in range(size):
            if result[r][c] is not None: result[r][c]^=fn(r,c)
    return result

def build_qr_matrix(text):
    bits,ver=build_codewords(text)
    size=ver*4+17
    matrix=make_matrix(size)
    set_finder(matrix,0,0); set_finder(matrix,0,size-7); set_finder(matrix,size-7,0)
    set_separators(matrix,size); set_timing(matrix,size)
    set_dark_module(matrix,ver); set_alignment(matrix,ver)
    reserve_format(matrix,size); place_data(matrix,size,bits)
    masked=apply_mask(matrix,size,0)
    place_format(masked,size,0)
    return masked,size

def qr_to_svg(text,scale=8,quiet=4):
    masked,size=build_qr_matrix(text)
    total=(size+2*quiet)*scale
    rects=[]
    for r in range(size):
        for c in range(size):
            if masked[r][c]:
                x=(c+quiet)*scale; y=(r+quiet)*scale
                rects.append(f'<rect x="{x}" y="{y}" width="{scale}" height="{scale}"/>')
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="{total}" viewBox="0 0 {total} {total}">
<rect width="{total}" height="{total}" fill="white"/>
<g fill="black">{"".join(rects)}</g>
</svg>'''

def qr_to_png_bytes(text,scale=8,quiet=4):
    from PIL import Image,ImageDraw
    import io
    masked,size=build_qr_matrix(text)
    total=(size+2*quiet)*scale
    img=Image.new('RGB',(total,total),'white')
    draw=ImageDraw.Draw(img)
    for r in range(size):
        for c in range(size):
            if masked[r][c]:
                x=(c+quiet)*scale; y=(r+quiet)*scale
                draw.rectangle([x,y,x+scale-1,y+scale-1],fill='black')
    buf=io.BytesIO()
    img.save(buf,format='PNG')
    buf.seek(0)
    return buf
