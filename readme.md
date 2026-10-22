# AutoGrader: A Rule-Driven Evaluator for C Programs

## Current status — Completed through Stage 4B

AutoGrader is a student C-program submission application being developed into an automated grading agent. **The currently working version supports user login, teacher-created assignments, student C-file uploads, permanent submission records, and submission history. It does not yet compile, run, or grade uploaded programs.**

This README explains how to install and run the **current working version**.

## What we have completed

| Stage | Completed functionality |
|---|---|
| 1 — Setup and frontend | Python project and virtual environment; Streamlit interface; Git setup. |
| 2 — Database | SQLite database through SQLAlchemy, with `users`, `assignments`, and `submissions` tables. |
| 3A — Authentication | Teacher/student account creation, Argon2 password hashing, login, logout, and role-based dashboards. |
| 3B — Assignments | Teacher creates and views assignments; student views available assignments; backend teacher-role check. |
| 4A — Submission backend | Validates a UTF-8 `.c` file (nonempty, maximum 100 KiB, no NUL bytes), checks student role and assignment, saves source under a unique UUID filename, computes SHA-256, and records the submission in SQLite. |
| 4B — Student upload interface | Student chooses an assignment, uploads a `.c` file, receives a submission ID and hash, and views submission history. |

**Verified during development:** a sample C file was saved successfully through the Python backend; a C file uploaded through the Student Dashboard appeared in **My Submission History**.

## Technologies used in the current version

- Python 3.12 and Streamlit — application and interface.
- SQLite and SQLAlchemy — persistent database and Python database operations.
- `argon2-cffi` — password hashing and verification.
- Python standard library: `hashlib` (SHA-256), `uuid` (unique storage names), `pathlib` (file paths), `argparse` and `getpass` (account-creation utility).
- Python `venv`, `requirements.txt`, VS Code, and Git — development environment and dependency/version management.

Some additional packages may already be listed in `requirements.txt` for later stages; their presence does not mean their grading features are implemented.

## Requirements

- Python 3.12 (recommended to match our development environment).
- Git, if cloning from GitHub.
- An available local browser.
- macOS or Linux commands are shown below. Windows activation is noted separately.

**Docker and GCC are not required to run the current Stage 4B application.** They will be used later for isolated C compilation and execution.

## Installation and running — step by step

### 1. Open the project

If you already have the `Autograder` folder, open it in VS Code and open **Terminal → New Terminal**. Make sure the Terminal is in the folder containing `app.py` and `requirements.txt`.

If using GitHub, clone your actual repository (replace the placeholder):

```bash
git clone <YOUR_REPOSITORY_URL>
cd <REPOSITORY_FOLDER>
```

### 2. Create and activate a virtual environment

On macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

On Windows PowerShell:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
```

When active, the Terminal normally displays `(.venv)`.

### 3. Install project packages

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 4. Initialize the SQLite database (first setup only)

```bash
python -m database.init_db
```

The initialization script creates `data/autograder.db` and the three tables: `users`, `assignments`, and `submissions`. Existing database records are not intentionally deleted by this command.

### 5. Create your demonstration accounts (first setup only)

```bash
python -m database.create_user --role teacher --username teacher1
python -m database.create_user --role student --username student1
```

Enter and confirm a password for each account when prompted. Password input is hidden. Remember these passwords; **do not put them in the README or commit them to Git**. If the accounts already exist in your existing database, skip this step rather than trying to recreate them.

### 6. Start the application

```bash
python -m streamlit run app.py
```

Open the URL displayed by Streamlit, normally **http://localhost:8501**.

### 7. Try the working features

**Teacher workflow**

1. Log in with username `teacher1` and the teacher password created in Step 5.
2. Open **Create Assignment**.
3. Enter an assignment title and problem statement, then click **Create Assignment**.
4. Check **View Assignments** to see the saved assignment.
5. Click **Logout**.

**Student workflow**

1. Log in with username `student1` and the student password.
2. Select the teacher-created assignment.
3. Upload a UTF-8 `.c` file (up to 100 KiB). The repository includes `tests/sample_add.c` as a sample, if present.
4. Click **Submit Program**.
5. Check the confirmation for a **Submission ID** and **SHA-256 hash**.
6. Scroll to **My Submission History** to see the stored record.

An assignment must exist before a student can submit a file. Uploading a file **stores it only**; automatic compilation and grading are not yet available.

### 8. Stop the application

Press **Control + C** in the Terminal running Streamlit. To leave the virtual environment, run `deactivate`.

On a later day, open the project folder and run:

```bash
source .venv/bin/activate
python -m streamlit run app.py
```

You do not need to recreate accounts or reinitialize the database every time.

## Current project structure

```text
Autograder/
├── app.py                        # Streamlit frontend and dashboards
├── requirements.txt              # Python dependencies
├── .gitignore
├── database/
│   ├── __init__.py
│   ├── db.py                     # SQLAlchemy engine and session
│   ├── orm_models.py             # User, Assignment, Submission tables
│   ├── init_db.py                # Initialize the database
│   └── create_user.py            # Create teacher/student accounts
├── services/
│   ├── __init__.py
│   ├── authentication.py         # Login, account creation and role checks
│   ├── assignment_service.py     # Save and retrieve assignments
│   └── submission_service.py     # Validate and store C submissions
├── data/
│   └── autograder.db             # Created locally; ignored by Git
├── storage/
│   └── submissions/             # Uploaded C files; ignored by Git
└── tests/
    └── sample_add.c             # Sample source for manual testing
```

Local files such as the database and uploaded submissions should remain in `.gitignore` and should not be published to GitHub.

## Troubleshooting

| Problem | What to check |
|---|---|
| `ModuleNotFoundError` | Activate `.venv` and run `python -m pip install -r requirements.txt`. |
| Blank or outdated Streamlit page | Save `app.py` with **Command + S**; run `wc -c app.py` to confirm it is not empty; restart Streamlit. |
| Login fails | Use the username and the password created with `database.create_user`; check that the database was initialized and the account exists. |
| No available assignments | Log in as the teacher and create one first. |
| Upload rejected | Use a nonempty UTF-8 `.c` file of no more than 100 KiB. |
| `tests/sample_add.c` not found | Check the filename with `ls -l tests/`. The file must be named `sample_add.c`, not `sample.c`, if using that path. |
| Port 8501 already in use | Stop the previous Streamlit instance using **Control + C**. |

## Next phase — planned, not yet implemented

The professor's assignment requires a complete rule-driven grading agent. Upcoming work includes:

1. External instructor-editable JSON rules (metrics, weights, thresholds and resource limits) and Pydantic validation.
2. An evaluation coordinator and reproducible evaluation fingerprint/cache.
3. Restricted C compilation and execution using GCC and a hardened Docker-based sandbox.
4. **First Pass: Compile Check + Smoke Run + Static Sanity Check**, before analyzer dispatch. A timeout will trigger a suspected-infinite-loop flag and an appropriate partial report.
5. Nine analyzers: correctness, syntax, structure, modularity, logical/edge cases, complexity, memory safety, readability via local Ollama, and originality/similarity.
6. Weighted scoring, evidence-based counterexamples (input, expected output, actual output), explanatory feedback, and JSON/text reports.
7. End-to-end and reproducibility tests, usefulness/limitations, installation documentation and final demonstration.

**Security:** Do not execute uploaded C programs directly on the host or from the Streamlit process. Safe execution must be implemented and tested before grading is enabled. Similarity scores are flags for teacher review, not proof of plagiarism.

## Academic project

**Project:** AutoGrader: A Rule-Driven Evaluator for C Programs  
**Department:** Computer Science and Engineering  
**Submitted to:** Dr. Arijit Mondal
