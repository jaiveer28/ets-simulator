# Deploying ETS to a public link

This turns ETS into a URL like `https://ets-simulator.onrender.com` that
**anyone can open in any browser, with nothing to install**. It's free.

The project is already prepared for this: `wsgi.py`, `Procfile`, `render.yaml`,
`.gitignore`, and the production server (`gunicorn`) are all in place. You do the
two account steps below — they can't be automated for you because they need your
own logins.

---

## The idea in one line

Your code goes to **GitHub** (a place to store the code online), and **Render**
(a free host) runs it from there and gives you the public link.

---

## Step 1 — Put the code on GitHub

1. Install **Git** if you don't have it: https://git-scm.com/download/win
2. Create a free account at https://github.com and click **New repository**.
   Name it e.g. `ets-simulator`, keep it **Public**, and **don't** add a README
   (you already have one). Click **Create repository**.
3. In a terminal, from the project folder, run these (replace `YOUR-USERNAME`):

   ```powershell
   cd "C:\Users\mp_ma\OneDrive\Desktop\STOCK SIMULATOR"
   git init
   git add .
   git commit -m "ETS trading simulator"
   git branch -M main
   git remote add origin https://github.com/YOUR-USERNAME/ets-simulator.git
   git push -u origin main
   ```

   (`data/market.db` is included on purpose — the live app needs it. Your
   personal `simulations.db` is excluded by `.gitignore`.)

---

## Step 2 — Deploy on Render

1. Create a free account at https://render.com and connect it to your GitHub.
2. Click **New +** -> **Blueprint**.
3. Pick your `ets-simulator` repository. Render reads `render.yaml` and fills in
   everything automatically (free plan, the start command, and a randomly
   generated `SECRET_KEY`).
4. Click **Apply** / **Create**. Wait ~2-5 minutes for the first build.
5. When it finishes, Render shows your public URL at the top, e.g.
   `https://ets-simulator.onrender.com`. **That is the link you share.**

To update the site later, just `git push` again — Render redeploys on its own.

---

## What to expect (and tell people)

- **It works for anyone.** They open the link, they use ETS. No install, no login.
- **First visit may be slow.** On the free plan the app "sleeps" after ~15 min of
  no traffic; the next visit takes ~30-60 seconds to wake up, then it's fast.
- **Each visitor gets their own simulation** (via their browser session). They
  don't see each other's portfolios.
- **Progress isn't permanent on the free plan.** Render's free disk is wiped on
  each redeploy/restart, so saved simulations reset then. Fine for a demo; for
  permanent storage you'd add a Render persistent disk or a hosted database.

---

## Alternative: just share the code

If a reviewer is technical, you can simply send them the **GitHub link** from
Step 1. They read the `README.md`, run three commands, and it works on their
machine — and they can inspect how it's built, which is often what an application
reviewer actually wants.
