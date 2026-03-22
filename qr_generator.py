import qrcode
import qrcode.image.svg
import io


def qr_to_svg(text):
    factory = qrcode.image.svg.SvgPathImage
    img = qrcode.make(
        text,
        image_factory=factory,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=4,
    )
    buf = io.BytesIO()
    img.save(buf)
    buf.seek(0)
    return buf.read().decode('utf-8')


def qr_to_png_bytes(text):
    img = qrcode.make(
        text,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=4,
    )
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return buf