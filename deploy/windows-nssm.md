# Running callisto-jupiter as a Windows service (NSSM)

[NSSM](https://nssm.cc/) (the Non-Sucking Service Manager) runs any executable as
a Windows service with automatic restart and start-on-boot — no extra Python
dependency in the agent.

## 1. Install the agent

```powershell
py -m pip install "callisto-jupiter[gpu]"   # or without [gpu] on non-NVIDIA hosts
```

Find the interpreter that has it installed (used below):

```powershell
py -c "import sys; print(sys.executable)"
# e.g. C:\Users\you\AppData\Local\Programs\Python\Python312\python.exe
```

## 2. Configure

Create `C:\ProgramData\callisto-jupiter\config.toml` (the Windows default path):

```toml
dsn = "https://ingest.callistosignal.com/REPLACE-ID?token=REPLACE-TOKEN"
interval_seconds = 60
disk_path = "C:\\"
```

(Or skip the file and set `CALLISTO_DSN` as a service env var — see step 3.)

## 3. Install + start the service

```powershell
# Download nssm.exe from https://nssm.cc/download and put it on PATH.
nssm install callisto-jupiter "C:\path\to\python.exe" "-m" "callisto_jupiter"
nssm set callisto-jupiter AppDirectory "C:\ProgramData\callisto-jupiter"
nssm set callisto-jupiter Start SERVICE_AUTO_START
nssm set callisto-jupiter AppStdout "C:\ProgramData\callisto-jupiter\agent.log"
nssm set callisto-jupiter AppStderr "C:\ProgramData\callisto-jupiter\agent.err.log"

# Optional: configure via env instead of config.toml
nssm set callisto-jupiter AppEnvironmentExtra CALLISTO_DSN=https://ingest.callistosignal.com/REPLACE-ID?token=REPLACE-TOKEN

nssm start callisto-jupiter
```

## Manage

```powershell
nssm status  callisto-jupiter
nssm restart callisto-jupiter
nssm stop    callisto-jupiter
nssm remove  callisto-jupiter confirm
```

NSSM stops the service by terminating the process; the agent's interruptible
sleep means in-flight cycles end promptly. GPU metrics work on Windows NVIDIA
hosts (install the `[gpu]` extra); the `gpu` sample is omitted otherwise.
