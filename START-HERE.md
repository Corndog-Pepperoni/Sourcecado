# Your Sourcecado setup

The development environment is prepared. The six coding exercises are intentionally unfinished.
All eight exercise files match the original `starter` commit `59d56fa`.

## Use this copy

Open this folder in VS Code:

```text
C:\Users\cpedr\Documents\Codex\2026-11-16\https-app-notion-com-p-build\outputs\Sourcecado
```

Your earlier folder on the OneDrive desktop was not edited. Use this prepared copy so that the Windows setup fixes and your GitHub fork connection are available together.

Your repository is [Corndog-Pepperoni/Sourcecado](https://github.com/Corndog-Pepperoni/Sourcecado). A **repository** is a project folder with a history of its changes. A **fork** is your own GitHub copy of someone else's repository. You can save changes there without changing the instructor's project.

The local branch is called `main`, but its application code comes from the instructor's **starter** branch. A **branch** is a named line of development. This is the arrangement requested by step 0.4 of the guide.

## What is ready

| Item | Status | What it is for |
| --- | --- | --- |
| Git and GitHub CLI | Installed; GitHub CLI signed in | Save code changes and upload them to your fork |
| Python 3.14 | Installed; project environment created | Run the backend and its tests |
| Node 24 and npm | Installed; frontend dependencies installed | Run and build the browser interface |
| Docker Desktop | Running | Run the local database and container checks |
| Supabase CLI 3.2.0 | Installed in this repository | Manage the database from the terminal |
| Make | Available; guide commands adapted for Windows | Run groups of commands by a short name |
| Local Postgres database | Running on `127.0.0.1:53322` | Test your code without using the cloud database |
| Supabase cloud project | Created; 39 empty tables retained at your request | Store the deployed app's data later |
| Railway project and backend service | Created; root directory and health-check path prepared | Host the backend later |
| Vercel project | Created; configured for `frontend`, Vite, and Node 24 | Host the browser interface later |

**Dependencies** are libraries the project uses. Python's project environment is in `backend/.venv`; JavaScript libraries are in `node_modules`. These folders are installed locally and are not uploaded as source code.

The setup changes do not provide exercise answers. They select Windows's `Scripts` directory for Python, use the local Supabase CLI, fix the check runner when Make's path contains spaces, keep CSS/shell-script line endings consistent, and make existing brand images available on Windows.

## Start here: exercise 1

In VS Code, choose **File > Open Folder**, select the prepared folder above, and choose **Terminal > New Terminal**. The terminal should open in the project folder. You can confirm with:

```powershell
Get-Location
```

Keep Docker Desktop open. Then run:

```powershell
make db-up
make check-1
```

`make db-up` starts the local database, or reports that it is already running.
`make check-1` runs the first exercise's automatic checks. Before you write the code, a failure about `Database.tx` not being implemented is expected. The last line should say `PIECE 1: NOT YET`.

Open `backend/coworker/db.py`, find the two `YOUR TURN` comments, and follow their instructions. Run `make check-1` again after your edits. Your goal is `PIECE 1: PASS`.

If you ever need to reinstall dependencies, run `make setup` from the repository root. Setup has already been run successfully.

## Continue through the guide

| Exercise | Files you edit | Check command |
| --- | --- | --- |
| 1: database access | `backend/coworker/db.py` | `make check-1` |
| 2: meeting-candidate table | `supabase/migrations/20261110000800_meeting_evidence.sql` | `make check-2` |
| 3: container recipe | `backend/Dockerfile` | `make check-3` |
| 4: health address and startup rule | `backend/coworker/server.py`, `backend/coworker/run.py` | `make check-4` |
| 5: server sign-in check | `backend/coworker/hosted_auth.py` | `make check-5` |
| 6: browser sign-in | `frontend/src/auth.ts`, `frontend/src/api.ts` | `make check-6` |

Work in order. Later checks can need earlier exercises to be finished. `make check` runs the six checks together. A starter with unfinished exercises should report `NOT YET`; that does not mean the tools were installed incorrectly.

The database tests create temporary databases for their checks. They do not use the cloud project. Even though you chose to keep the cloud tables, you still need to complete the SQL exercise in your local migration file.

After you finish a block and its checks pass, save it to your fork:

```powershell
git status
git add -A
git commit -m "Block 1: my code"
git push origin main
```

Change the message to match the block you completed. `git status` lists changed files; `git add` selects changes to save; `git commit` creates a local save point; `git push` uploads your commits.

## Windows differences from the original guide

Use PowerShell in the prepared repository folder. The Makefile handles its internal Bash commands for you.

- Use `python3.14`, not the Windows Store `python` alias on this computer.
- Use `npx --no-install supabase` wherever the guide says `supabase`. For example, `npx --no-install supabase --version`.
- The Supabase CLI inside this repository is version 3. For cloud changes it uses `--remote`; inspect a command's current flags with `--help` before using the guide's cloud CLI steps. For example: `npx --no-install supabase db push --help`.
- To count the starter comments without Unix `grep`, use `(Select-String -LiteralPath backend/coworker/db.py -Pattern 'YOUR TURN').Count`. The expected number is `2`.
- For an HTTP check, use `curl.exe` so PowerShell does not substitute a different command.
- `127.0.0.1` means this computer. A **port**, such as `53322`, identifies which local service a connection should reach.

## Your cloud projects

- [Supabase project](https://supabase.com/dashboard/project/rmmypkrykxcxjbalrnzm) — API address: `https://rmmypkrykxcxjbalrnzm.supabase.co`
- [Railway project](https://railway.com/project/9d0afda2-cfee-4ee1-9845-baf2007cd2a6) — service name: `backend`; root directory: `backend`; health-check path: `/healthz`
- [Vercel project](https://vercel.com/sourcecado/sourcecado) — root directory: `frontend`; build command: `npm run build`; output directory: `dist`; Node 24

These projects are prepared but the application is **not deployed**. No public backend domain was created. Google OAuth is not configured, and no live AI key or cloud database password has been added to the app.

You asked to keep the 39 empty cloud tables as setup. Their five migration versions match the repository's five migration filenames. Once your SQL exercise is complete, the guide's database upload step may have no new migrations to apply.

Supabase quoted $0/month when this project was created. Running a backend and using a real AI provider can consume credits or incur costs; check your service plans before the deployment stage. No paid plan was purchased during setup.

## The app's parts, in plain language

The **frontend** is the page you see in your browser. **React** is the library used to build that page; **Vite** runs it during development and prepares the files for hosting. Vercel will serve those files online.

The **backend** is the Python program that handles requests, checks sign-in, calls the AI, and reads or writes stored data. **FastAPI** provides its web routes. Railway will run this program inside a **container**, a package containing the program and its dependencies. The **Dockerfile** describes how to build that package.

**Postgres** is the database engine. **Supabase** supplies Postgres plus authentication and other hosted services. A **table** resembles a spreadsheet; a **row** is one record; a **column** is one field. **SQL** is the language used to define tables and read or change records. A **migration** is a saved change to the database structure.

**Authentication** establishes who signed in. **Authorization** decides what that person may access. **OAuth** is the sign-in handoff between your app, Supabase, and Google. A **token** is signed evidence of a session; a **JWT** is the token format used here. The backend checks that evidence and the invitation list before allowing access.

An **environment variable** is a named setting supplied to a program, such as the database address. Frontend variables beginning with `VITE_` can appear in the browser's built files, so they must never contain private credentials. Keep private passwords and API keys out of Git commits.

A **mock AI** returns a fixed test reply without calling a real model. It helps you check the surrounding app. A **health check** is a small address the host calls to see whether the server is alive. **Deployment** means putting the finished program on its online host; it is a later step in your guide.
