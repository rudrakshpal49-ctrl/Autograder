# AutoGrader — Rule-Driven C Programming Evaluator

A local, teacher-managed **C assignment grading prototype** built with Python, Streamlit, SQLite, SQLAlchemy, GCC in Docker, Lizard, and optional local Ollama.

Teachers can upload assignment documents, set deadlines, manage students, generate and approve test cases, and inspect evaluation results. Students can view assignments, upload `.c` files, and see their submission history; **grades, hidden tests, compiler diagnostics, and source hashes are teacher-only**.

> **Current status (October 2026):** Frontend, database, First Pass (compile, smoke run, static sanity), Correctness, Syntax, Structure, and Modularity are implemented. Logical/Edge Cases, Memory Safety, Complexity, Readability, Originality, and the final combined nine-analyzer grading report are **not yet complete**. Do not treat the individual analyzer scores as a finished overall grade.

## 1. Requirements

| Component | Required? | Purpose |
| --- | --- | --- |
| **Python 3.12 or 3.13** | Yes | Runs the application and Python analyzers |
| **Python packages** (`requirements.txt`) | Yes | Streamlit, SQLAlchemy, Argon2, PDF parser, Lizard, pytest |
| **Docker Desktop (Windows/macOS) or Docker Engine/Desktop (Linux)** | Yes for compiling/running C | Isolated execution of student submissions |
| **`gcc:13` Docker image** | Yes for compiling/running C | GCC 13 toolchain inside containers; **no separate host GCC installation required** |
| **Ollama** | Optional | Locally generates candidate correctness tests and suggests rubric limits |
| **`llama3.2:3b` model** | Optional, if using Ollama features | Default model used by the project |

This README includes full setup for **Windows (PowerShell)**, **macOS (Terminal / zsh)**, and **Linux (Ubuntu / bash)**. Run project commands from the **AutoGrader project root** (the folder containing `app.py`, `database/`, and `requirements.txt`). Docker and Ollama are installed separately from Python; neither belongs in `requirements.txt`.

## 2. Quick start (Windows, from scratch)

### Step A — Python environment

Check that Python is available:

```powershell
py --version
```

Open your AutoGrader folder in VS Code, then in its PowerShell terminal:

```powershell
# Skip this cd if the terminal is already in your project folder.
cd "C:\path\to\AutoGrader"

# Create a project-specific virtual environment (once).
py -m venv .venv

# Activate the virtual environment (each new PowerShell session).
.\.venv\Scripts\Activate.ps1

# Install/upgrade Python dependencies (once, and after requirements changes).
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If `py` is not available but `python` works, replace `py` with `python` in the environment-creation command. If PowerShell blocks activation, **do not change your machine-wide execution policy**; instead use the virtual environment's executable directly:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m database.init_db
.\.venv\Scripts\python.exe -m streamlit run app.py
```

### Step B — Install and verify Docker Desktop

On Windows, Docker Desktop normally uses **WSL 2 with Linux containers**. See the official installation instructions: https://docs.docker.com/desktop/setup/install/windows-install/

If WSL is not installed, open **PowerShell as Administrator** and run:

```powershell
wsl --install
wsl --update
```

Restart Windows if instructed. Install Docker Desktop using its official installer, start Docker Desktop, and make sure it is using **Linux containers**. In a **new** PowerShell window:

```powershell
wsl --version
docker version
docker info
docker pull gcc:13
docker image inspect gcc:13
docker run --rm gcc:13 gcc --version
```

`docker pull gcc:13` requires internet access the first time. The application runs submitted C programs **only inside Docker** and intentionally has no unsafe host-execution fallback.

### Step C — Optional: Install Ollama for automatic test generation

Install Ollama for Windows from https://ollama.com/download/windows and start it. The model uses local machine resources; a CPU-only system can be slow.

In a **new** PowerShell terminal:

```powershell
ollama --version
ollama pull llama3.2:3b
ollama list
ollama run llama3.2:3b
```

Type `/bye` to exit the interactive Ollama session. Check that the local API is accessible:

```powershell
Invoke-RestMethod http://127.0.0.1:11434/api/tags
ollama ps
```

On Windows, Ollama usually runs in the background. If it does not, start the Ollama app; use `ollama serve` only when no Ollama server is already running.

If `ollama` is not recognized, restart VS Code/PowerShell after installing. You can also verify its default installation path:

```powershell
Test-Path "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
& "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" --version
```

**Ollama is optional:** teachers can provide manual test cases and use automatic default grading rules without it. Docker is still required to execute those cases.

### Step D — Initialize the database and teacher account

From the project root, with the virtual environment active:

```powershell
python -m database.init_db
python -m database.create_user --role teacher --username teacher1
```

The second command prompts you for a password and confirmation; use **at least 12 characters**. For existing projects, `database.init_db` performs additive migrations and is intended to preserve existing records.

Only create a teacher account if you do not already have one. The teacher can create student accounts through the UI.

### Step E — Start AutoGrader

```powershell
python -m streamlit run app.py
```

Open the local address displayed by Streamlit, typically **http://localhost:8501**.

To stop the app, press **Ctrl+C** in the Streamlit terminal. To run it on a different port:

```powershell
python -m streamlit run app.py --server.port 8502
```

### Step F — Run the tests

```powershell
python -m pytest -q
```

Tests exercise database/service logic and use mocks for external Docker/Ollama operations. **Passing unit tests does not prove that Docker, Ollama, or the browser UI work on your machine**. Perform the live smoke test in section 6.

## 3. Quick start (macOS — MacBook, Apple silicon or Intel)

These commands work in the **Terminal** app or the VS Code integrated terminal. macOS uses `python3` before activation, then `python` inside a virtual environment. **You do not need to install GCC locally**: the project uses GCC inside Docker.

### Step A — Install/check Python and create the environment

Check your installed version; this project is recommended with **Python 3.12 or 3.13**:

```bash
python3 --version
```

If you need a compatible Python version, install it from https://www.python.org/downloads/macos/ or use [Homebrew](https://brew.sh/) if already installed:

```bash
brew install python@3.13
python3.13 --version
```

From your downloaded/cloned project directory:

```bash
cd ~/path/to/AutoGrader  # Change to your actual folder

# Use python3.13 if installed, otherwise use a compatible python3.
python3 -m venv .venv
source .venv/bin/activate
python --version
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If the system `python3` is not 3.12 or 3.13, use `python3.13 -m venv .venv` instead. Activate the environment with `source .venv/bin/activate` in each new terminal. Run `deactivate` when done.

### Step B — Install Docker Desktop for Mac

1. Check your chip in **Apple menu → About This Mac** (Apple silicon / Intel).
2. Download the matching Docker Desktop installer from https://docs.docker.com/desktop/setup/install/mac-install/.
3. Open the downloaded `.dmg`, move **Docker.app** to Applications, and open Docker (the app may request installation permissions). Wait until Docker's engine is running.

Verify Docker and pull the GCC image:

```bash
open -a Docker
# Docker Desktop must be fully started before the next commands.
docker version
docker info
docker pull gcc:13
docker run --rm gcc:13 gcc --version
```

Docker Desktop handles Linux containers on a Mac, including Apple silicon Macs. If `docker info` cannot reach the engine, open Docker Desktop and verify it finished starting. The first pull needs internet access.

### Step C — Optional: install Ollama on macOS

Install the macOS application from https://ollama.com/download (choose macOS), open **Ollama**, then use Terminal:

```bash
ollama --version
ollama pull llama3.2:3b
ollama list
ollama run llama3.2:3b
```

Enter `/bye` to exit Ollama's interactive chat, then confirm the local API is available:

```bash
curl -fsS http://127.0.0.1:11434/api/tags
ollama ps
```

If the API does not respond, ensure the Ollama app is running. Do not start a second `ollama serve` if its server is already active.

### Step D — Initialize the database, create a teacher and start the app

```bash
# Run these from the project root with .venv active.
python -m database.init_db
python -m database.create_user --role teacher --username teacher1
python -m pytest -q
python -m streamlit run app.py
```

Use at least a 12-character password when prompted. Existing users can skip account creation. Open http://localhost:8501 in your browser. Stop the app with **Ctrl+C**.

---

## 4. Quick start (Linux — Ubuntu / Debian-family)

These commands are for a **local Ubuntu workstation using bash**, with notes for other Linux distributions. You can use Docker Desktop for Linux instead of the Engine steps below. You do **not** need a host GCC installation for this project.

### Step A — Install/check Python and prepare a virtual environment

On Ubuntu:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip ca-certificates curl
python3 --version
```

The project is recommended with **Python 3.12 or 3.13**. If your distribution defaults to another version, install a supported version from your distribution's supported packages or an official Python distribution, then use that binary (for example, `python3.13`) to create the environment. Do not replace your Linux system's `python3` executable.

```bash
cd ~/path/to/AutoGrader  # Change to your actual folder
python3 -m venv .venv
source .venv/bin/activate
python --version
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If you installed `python3.13`, use `python3.13 -m venv .venv`. Reactivate via `source .venv/bin/activate` when opening a new terminal.

### Step B — Install Docker Engine on Ubuntu (or Docker Desktop)

**Simpler graphical option:** install Docker Desktop for Linux from https://docs.docker.com/desktop/setup/install/linux/ (follow the instructions for your distribution). Verify `docker info` works without `sudo` before launching AutoGrader.

**Ubuntu Docker Engine option:** follow Docker's official Ubuntu setup at https://docs.docker.com/engine/install/ubuntu/. On a clean supported Ubuntu installation, the following registers Docker's official apt repository and installs its Engine:

```bash
sudo apt update
sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo docker run --rm hello-world
```

If you have an existing Docker installation or conflicting `docker.io` packages, **consult the official Docker installation page before running these install commands**. On Debian, Fedora, Arch, or other distributions, use their respective distribution-specific Docker instructions rather than the Ubuntu repository above: https://docs.docker.com/engine/install/.

**Important Linux permissions:** The AutoGrader process invokes `docker` as the logged-in user, not through `sudo`. A setup where only `sudo docker ...` works will not be sufficient. Prefer Docker Desktop or a correctly configured rootless Docker setup; see https://docs.docker.com/engine/security/rootless/. On a trusted local development workstation you may choose Docker's conventional group method:

```bash
sudo usermod -aG docker "$USER"
# Completely log out and log in again for group membership to take effect.
docker info
docker pull gcc:13
docker run --rm gcc:13 gcc --version
```

**Security warning:** membership in the `docker` group grants root-level privileges. Use that method only if you understand the implications. See https://docs.docker.com/engine/install/linux-postinstall/. Do not run Streamlit with `sudo` to work around permissions.

### Step C — Optional: install Ollama on Linux

Install Ollama from its official Linux instructions: https://ollama.com/download/linux. The official installer command is below; review the script if required by your machine's security policy:

```bash
curl -fsSL https://ollama.com/install.sh -o ollama-install.sh
less ollama-install.sh
sh ollama-install.sh
```

On a standard systemd installation, start or verify the service:

```bash
sudo systemctl enable --now ollama
ollama --version
ollama pull llama3.2:3b
ollama list
curl -fsS http://127.0.0.1:11434/api/tags
ollama ps
```

If the installer did not create a service, follow its printed instructions, or launch `ollama serve` in a separate terminal (only if no server is already running). Ollama is optional; manual teacher test cases work without it.

### Step D — Initialize, test and run AutoGrader

```bash
# Run from the project root with .venv active.
python -m database.init_db
python -m database.create_user --role teacher --username teacher1
python -m pytest -q
python -m streamlit run app.py
```

Open http://localhost:8501. Use **Ctrl+C** to stop. To test Docker from the same user running Python, execute `docker info` in that same terminal.

---

## 5. Project workflow

### Teacher

1. Sign in to the **Teacher Dashboard**.
2. Open **Assignments** → create an assignment. Upload a **PDF/DOCX/TXT/MD** file, set a deadline, and optionally provide **Sample stdin** and **Sample stdout**.
3. If no grading JSON is uploaded, the app creates **default grading rules automatically**. Custom JSON rules remain optional.
4. Under **Automatic grading configuration**, review saved Structure/Modularity thresholds. Optionally click **Improve with Ollama & save**, or use advanced manual overrides. Rules are saved directly without downloading/re-uploading JSON.
5. Open the assignment's **Correctness tests** section → **Generate test cases from assignment PDF**. The saved sample stdin/stdout guide Ollama. If Ollama is unavailable, use **teacher-provided inputs** instead.
6. Inspect/edit generated test cases and their **expected outputs**. Optionally upload a trusted C reference solution to check outputs. Click **Prepare and preview expected outputs**, then **Approve and freeze these tests**.
7. Open **Students** to create/manage student accounts.
8. Open **Submissions** to inspect submitted `.c` code and run **First Pass**, **Correctness**, and **three easy analyzers** (Syntax, Structure, Modularity). Detailed results are **teacher-only**.

### Student

1. Log in with credentials created by the teacher.
2. Open **Assignments** to download the assignment and see its deadline.
3. Submit a UTF-8 `.c` file before the deadline.
4. See upload confirmation and submission history. Students **do not** see grades, hidden test cases, source hashes, or internal diagnostics.

### How Correctness works

```text
Assignment document + optional sample stdin/stdout
                  |
                  v
      Ollama generates candidate tests
        (or teacher supplies inputs)
                  |
                  v
  Validate known formats, check duplicates,
  and verify outputs where a trusted method exists
                  |
                  v
        Teacher reviews and approves
                  |
                  v
        Docker runs submitted C code
                  |
                  v
     Compare actual vs expected stdout
                  |
                  v
         Save teacher-only result
```

**Important:** An LLM-predicted expected output is not automatically authoritative. Teachers should verify it before approval; a reference C solution is a more general verification option. The validator supports selected common input formats, not arbitrary C assignments with guaranteed accuracy.

## 6. Live test checklist

Once both Docker and the app are running:

1. Create an assignment for adding two integers (or upload one of your array/string assignment PDFs).
2. Supply appropriate **Sample stdin**, e.g. `5 7` for addition, and optional **Sample stdout**, e.g. `12`.
3. Create a student account and sign in as that student.
4. Upload a valid `.c` source file. If your local `tests/sample_add.c` is a valid standalone C file, you can use it; otherwise, create a simple valid example.
5. Sign back in as teacher → **Submissions** → **Run first-pass checks**.
6. Check that compilation succeeds and the smoke run finishes without timeout/crash. **Smoke run does not verify correctness.**
7. Generate, inspect, and approve correctness tests; run **Run correctness tests** for the submission.
8. Use **Run 3 easy analyzers** to inspect syntax, structure, and modularity scores.

## 7. Current grading methodology

| Module | How it works | Status |
| --- | --- | --- |
| First Pass | GCC compile, sample-input smoke run, source sanity | Implemented |
| Correctness | Approved test cases; Docker output comparison | Implemented |
| Syntax | GCC error/warning analysis | Implemented |
| Structure | Lizard cyclomatic complexity + **non-comment LOC (NLOC)**, soft thresholds (70%/30%) | Implemented |
| Modularity | **Automatic**, **Required**, or **Disabled** modes; helpers required only when configured | Implemented |
| Logical/Edge Cases | Boundary and equivalence-partition tests | Planned |
| Memory Safety | AddressSanitizer / UndefinedBehaviorSanitizer | Planned |
| Time Complexity | Static/dynamic complexity estimation | Planned |
| Readability | Deterministic rubric, optional AI advice | Planned |
| Originality | Similarity flags for teacher review | Planned |
| Weighted final grade and reports | Combine all configured metrics | Planned |

Structure and Modularity rules are stored per assignment; they should not be regenerated per student. The automatic rubric has fixed defaults; Ollama can **optionally** suggest changes, which are validated and saved as new rules. Existing evaluations may need rerunning after rule changes.

## 8. Useful commands

Run from the project root unless noted.

```powershell
# Restart the app
python -m streamlit run app.py

# Create another teacher
python -m database.create_user --role teacher --username teacher2

# Update the schema without deleting records
python -m database.init_db

# Run all tests
python -m pytest -q

# Run one test file
python -m pytest -q tests/test_correctness.py

# Verify installed Python dependencies
python -m pip check
python -m pip list

# Check Docker and the compiler image
docker info
docker images gcc
docker run --rm gcc:13 gcc --version

# Check Ollama and its model
ollama list
ollama ps
Invoke-RestMethod http://127.0.0.1:11434/api/tags
```

If local Ollama generation is slow, you can adjust its request timeout for **this terminal session**, then launch Streamlit in the same session. On Windows PowerShell:

```powershell
$env:AUTOGRADER_OLLAMA_TIMEOUT_S = "90"
$env:AUTOGRADER_OLLAMA_MODEL = "llama3.2:3b"
$env:AUTOGRADER_TEST_RETRIES = "1"
python -m streamlit run app.py
```

Supported generated-test counts in the teacher UI are **3, 6, or 9**; smaller counts are faster. Timeout values are clamped by the application to 10–180 seconds per request.

On macOS/Linux (bash/zsh), use `export` instead of PowerShell's `$env:` syntax:

```bash
export AUTOGRADER_OLLAMA_TIMEOUT_S=90
export AUTOGRADER_OLLAMA_MODEL=llama3.2:3b
export AUTOGRADER_TEST_RETRIES=1
python -m streamlit run app.py
```

### Back up the SQLite database before updating code

Stop Streamlit first; then run:

```powershell
# Only if the DB file already exists
Copy-Item .\data\autograder.db .\data\autograder.backup.db
```

On macOS/Linux, after stopping Streamlit:

```bash
[ -f data/autograder.db ] && cp data/autograder.db data/autograder.backup.db
```

**Do not delete `data/` or `storage/`** when replacing project code. Student source files and assignment uploads are separate from the SQLite database. Never commit real student uploads or the SQLite database to Git.

## 9. Troubleshooting

| Issue | Check or fix |
| --- | --- |
| `python` or `py` not recognized | Windows: check `py --version`; macOS/Linux: check `python3 --version` and activate `.venv` |
| `streamlit` not recognized | Use `python -m streamlit run app.py` from the project virtual environment |
| PowerShell blocks `.venv` activation | Run `.\.venv\Scripts\python.exe` directly as shown in Step A |
| Docker not found | Install Docker Desktop; reopen terminal |
| Docker engine not running | Windows/Mac: start Docker Desktop; Linux Engine: check `sudo systemctl status docker`; run `docker info` |
| Linux `docker` permission denied | The Python user must access Docker **without `sudo`**. See Linux Docker permissions above (rootless or Docker group) |
| GCC image missing | Run `docker pull gcc:13` |
| Ollama not recognized | Install Ollama, reopen VS Code/PowerShell, verify executable path in Step C |
| Ollama connection error | Windows: `Invoke-RestMethod http://127.0.0.1:11434/api/tags`; Mac/Linux: `curl -fsS http://127.0.0.1:11434/api/tags` |
| Ollama model unavailable | Run `ollama pull llama3.2:3b` |
| Ollama takes too long | Use 3 test cases; run `ollama ps`; optionally increase `AUTOGRADER_OLLAMA_TIMEOUT_S` |
| Generated inputs are wrong | Provide Sample stdin/stdout; review and edit cases or supply manual tests |
| Generated expected output is wrong | Correct it before approval or use a teacher reference solution |
| `Smoke run · Skipped` | Add Sample stdin or approve correctness tests, then rerun First Pass |
| PDF has no selectable text | Upload a text-based PDF, DOCX, TXT, or MD file; scanned PDFs require separate OCR support |
| Database tables/columns missing | Run `python -m database.init_db` |
| Old analyzer result after changing rules | Rerun the analyzer; results may be stale after rubric changes |

## 10. Security and current limitations

- Submitted C code runs only in temporary, restricted Docker containers. Containers **reduce risk but are not a complete security guarantee**; this is an academic/local prototype, not a public multi-tenant execution service.
- PDF-driven AI test generation can misunderstand problem statements, input formats, and expected outputs. Teacher approval is mandatory.
- Static sanity checks are deliberately approximate. Lack of a `scanf()` or explicit `return` is not necessarily an error in a valid C program.
- Only the **four currently implemented analyzers** are available; a complete weighted nine-analyzer grade is not yet produced.
- Automated tests mock some integrations. Verify Docker, Ollama, and UI locally before demonstrating or grading real work.

## 11. Project layout

```text
AutoGrader/
├── app.py                         # Streamlit UI
├── requirements.txt               # Direct Python dependencies
├── database/
│   ├── db.py                      # SQLite connection
│   ├── init_db.py                 # Create/migrate tables
│   ├── create_user.py             # CLI to create accounts
│   └── orm_models.py
├── services/                       # Auth, assignments, students, submissions, tests, rules
├── agent/
│   ├── sandbox.py                 # Docker GCC compile and execution
│   ├── first_pass.py
│   ├── test_generator.py          # Ollama / document extraction
│   ├── test_validation.py
│   └── rubric_recommender.py
├── analyzers/
│   ├── correctness.py
│   ├── syntax.py
│   ├── structure.py
│   ├── modularity.py
│   └── source_metrics.py
├── rules/example_rules.json
├── tests/
├── data/autograder.db             # Created locally (not committed)
└── storage/                       # Private assignment/submission files
```

**Official installation references**

- Python downloads and virtual environments: https://www.python.org/downloads/ and https://docs.python.org/3/tutorial/venv.html
- Docker Desktop (Windows): https://docs.docker.com/desktop/setup/install/windows-install/
- Docker Desktop (macOS): https://docs.docker.com/desktop/setup/install/mac-install/
- Docker Desktop (Linux): https://docs.docker.com/desktop/setup/install/linux/
- Docker Engine (Linux): https://docs.docker.com/engine/install/
- Docker Engine (Ubuntu): https://docs.docker.com/engine/install/ubuntu/
- Docker Linux permissions: https://docs.docker.com/engine/install/linux-postinstall/
- Ollama (Windows): https://ollama.com/download/windows
- Ollama (macOS): https://ollama.com/download
- Ollama (Linux): https://ollama.com/download/linux
