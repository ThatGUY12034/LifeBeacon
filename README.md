# LifeBeacon — Vercel + Neon Deployment Guide

---

## STEP 1 — Get a Free PostgreSQL Database (Neon)

1. Go to https://neon.tech
2. Click **Sign Up** (use GitHub login — fastest)
3. Click **Create Project**
   - Name: `lifebeacon`
   - Region: pick closest to you
4. Click **Create Project**
5. You'll see a screen with your connection string. Copy it — looks like:
   ```
   postgresql://username:password@ep-xxxx.us-east-1.aws.neon.tech/neondb?sslmode=require
   ```
   ⚠️ Save this — you'll need it in Step 4

---

## STEP 2 — Push Code to GitHub

1. Go to https://github.com → **New Repository**
   - Name: `lifebeacon`
   - Set to **Public**
   - Click **Create Repository**

2. On your computer, open terminal in the `lifebeacon` folder:
   ```bash
   git init
   git add .
   git commit -m "Initial commit"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/lifebeacon.git
   git push -u origin main
   ```

---

## STEP 3 — Deploy on Vercel

1. Go to https://vercel.com
2. Click **Sign Up** → **Continue with GitHub**
3. Click **Add New Project**
4. Find your `lifebeacon` repo → Click **Import**
5. Leave all settings as default
6. Click **Deploy**

It will fail — that's expected. We need to add environment variables first.

---

## STEP 4 — Add Environment Variables

After deploy (even if it failed):

1. In Vercel dashboard → Click your project → **Settings**
2. Click **Environment Variables** in the left sidebar
3. Add these two variables:

   | Name | Value |
   |------|-------|
   | `DATABASE_URL` | paste your Neon connection string |
   | `SECRET_KEY` | any random long string e.g. `lifebeacon_super_secret_key_2024_xyz` |

4. Click **Save** for each one
5. Go to **Deployments** tab → Click the three dots → **Redeploy**

---

## STEP 5 — Done! 🎉

Your app is now live at:
```
https://lifebeacon-xxxxx.vercel.app
```

- Register as a patient
- Fill in your medical profile
- Go to **My QR** → scan the QR with your phone from anywhere in the world
- The emergency view opens instantly — no login needed

---

## File Structure

```
lifebeacon/
├── app.py              ← Flask app (PostgreSQL version)
├── qr_generator.py     ← Pure Python QR code generator
├── vercel.json         ← Vercel deployment config
├── requirements.txt    ← flask, psycopg2-binary, pillow
└── templates/
    ├── base.html
    ├── index.html
    ├── login.html
    ├── register.html
    ├── dashboard.html
    ├── profile.html
    ├── qr.html
    ├── emergency.html
    ├── doctor.html
    └── doctor_patient.html
```

---

## Troubleshooting

**"Application error" on Vercel:**
- Check that DATABASE_URL and SECRET_KEY are set correctly
- Go to Vercel → your project → **Functions** tab → click the function → **View Logs**

**"could not connect to server":**
- Your Neon connection string might be wrong
- Go to Neon dashboard → your project → **Connection Details** → copy again

**QR code not scanning:**
- Make sure you're using the live Vercel URL (not localhost)
- The QR embeds your public Vercel URL so it works from any phone

---

## For Cloud Computing Project Report

Mention these services:
- **Vercel** — Serverless cloud hosting (PaaS)
- **Neon** — Managed cloud PostgreSQL database (DBaaS)
- **GitHub** — Source code and CI/CD pipeline
- **Cloud Architecture** — Stateless Flask app + cloud DB = horizontally scalable

Architecture diagram for your report:
```
User's Browser / Phone
        ↓
   Vercel CDN Edge
        ↓
   Flask App (Serverless Function)
        ↓
   Neon PostgreSQL (Cloud DB)
```
