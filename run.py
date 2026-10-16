#!/usr/bin/env python3
"""Cross-platform launcher for heliograph.

Makes sure heliograph runs inside a virtual environment with every package in
requirements.txt installed, then hands all arguments through to heliograph.py:

  1. If a virtual environment is already active, use it.
  2. Otherwise use the project's .venv, creating it first if it doesn't exist.
  3. Check every requirement is installed (and satisfies its version specifier);
     install anything that is missing.
  4. Run heliograph.py with the same arguments, and exit with its exit code.

Works on Windows, macOS and Linux using only the standard library:

    python run.py flash secret.pdf          (Windows: py run.py ... works too)
    ./run.sh flash secret.pdf               (macOS / Linux)
    run.bat flash secret.pdf                (Windows)

Set HELIOGRAPH_VENV to use a different venv directory than ./.venv.
pip's own environment variables (PIP_NO_INDEX, PIP_FIND_LINKS, ...) are
honoured, which is how to install from carried-over wheels on an offline box.
"""

import os
import subprocess
import sys

MIN_PYTHON = (3, 8)

ROOT = os.path.dirname(os.path.abspath(__file__))
REQUIREMENTS = os.path.join(ROOT, "requirements.txt")
ENTRY_POINT = os.path.join(ROOT, "heliograph.py")
VENV_DIR = os.path.abspath(os.environ.get("HELIOGRAPH_VENV") or os.path.join(ROOT, ".venv"))
IS_WINDOWS = os.name == "nt"

# Runs *inside* the target interpreter and prints one unmet requirement per
# line. Uses `packaging` (vendored by pip) for full specifier/marker support,
# and falls back to a name-only check if it can't be imported.
CHECK_SCRIPT = r"""
import sys
try:
    from importlib.metadata import version, PackageNotFoundError
except ImportError:  # Python < 3.8
    from importlib_metadata import version, PackageNotFoundError
try:
    from packaging.requirements import Requirement
except ImportError:
    try:
        from pip._vendor.packaging.requirements import Requirement
    except ImportError:
        Requirement = None

def name_of(line):
    for sep in "[<>=!~;@ ":
        line = line.split(sep, 1)[0]
    return line.strip()

for raw in open(sys.argv[1], encoding="utf-8"):
    line = raw.split("#", 1)[0].strip()
    if not line or line.startswith("-"):
        continue
    if Requirement is not None:
        req = Requirement(line)
        if req.marker is not None and not req.marker.evaluate():
            continue
        try:
            installed = version(req.name)
        except PackageNotFoundError:
            print(line); continue
        if req.specifier and not req.specifier.contains(installed, prereleases=True):
            print(line)
    else:
        try:
            version(name_of(line))
        except PackageNotFoundError:
            print(line)
"""


def log(msg):
    print("[heliograph] " + msg, file=sys.stderr)


def die(msg, code=1):
    log("ERROR: " + msg)
    sys.exit(code)


def active_env_python():
    """Return the interpreter of an already-active environment, or None."""
    # Running under a venv/virtualenv interpreter.
    if sys.prefix != getattr(sys, "base_prefix", sys.prefix) or hasattr(sys, "real_prefix"):
        return sys.executable
    # An activated conda environment counts too, as long as it is this interpreter.
    conda = os.environ.get("CONDA_PREFIX")
    if conda and os.path.normcase(os.path.realpath(conda)) == os.path.normcase(os.path.realpath(sys.prefix)):
        return sys.executable
    # A venv is activated in the shell, but we were started with another
    # interpreter (e.g. `py run.py` on Windows). Prefer the activated one.
    venv = os.environ.get("VIRTUAL_ENV")
    if venv:
        python = venv_python(venv)
        if os.path.isfile(python):
            return python
    return None


def venv_python(venv_dir):
    if IS_WINDOWS:
        return os.path.join(venv_dir, "Scripts", "python.exe")
    return os.path.join(venv_dir, "bin", "python")


def run(cmd, **kwargs):
    return subprocess.call(cmd, **kwargs)


def has_pip(python):
    return run([python, "-m", "pip", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0


def venv_help():
    if sys.platform.startswith("linux"):
        ver = "%d.%d" % sys.version_info[:2]
        return ("On Debian/Ubuntu install the venv module first, e.g.\n"
                "    sudo apt install python3-venv   (or python%s-venv)\n"
                "On Fedora/RHEL: sudo dnf install python3-pip" % ver)
    return "Reinstall Python from https://www.python.org/downloads/ (includes venv and pip)."


def ensure_project_venv():
    """Return the project venv's interpreter, creating the venv if needed."""
    python = venv_python(VENV_DIR)
    if os.path.isfile(python) and has_pip(python):
        return python

    if os.path.isfile(python):
        # Venv exists but has no pip (e.g. a previous creation half-failed).
        log("Virtual environment at %s has no pip; trying to bootstrap it..." % VENV_DIR)
        if run([python, "-m", "ensurepip", "--upgrade", "--default-pip"]) == 0 and has_pip(python):
            return python
        log("Could not bootstrap pip; recreating the virtual environment.")
        clear = True
    else:
        log("No virtual environment found; creating one at %s ..." % VENV_DIR)
        clear = os.path.exists(VENV_DIR)

    cmd = [sys.executable, "-m", "venv"]
    if clear:
        cmd.append("--clear")
    if run(cmd + [VENV_DIR]) != 0 or not os.path.isfile(python) or not has_pip(python):
        die("Failed to create a working virtual environment at %s.\n%s" % (VENV_DIR, venv_help()))
    return python


def missing_requirements(python):
    if not os.path.isfile(REQUIREMENTS):
        return []
    proc = subprocess.run([python, "-c", CHECK_SCRIPT, REQUIREMENTS],
                          stdout=subprocess.PIPE, universal_newlines=True)
    if proc.returncode != 0:
        # Can't tell what's installed; let pip work it out.
        return ["(could not verify requirements)"]
    return [line for line in proc.stdout.splitlines() if line.strip()]


def ensure_requirements(python):
    missing = missing_requirements(python)
    if not missing:
        return
    log("Installing missing requirements: " + ", ".join(missing))
    if run([python, "-m", "pip", "install", "--disable-pip-version-check", "-r", REQUIREMENTS]) != 0:
        die("pip could not install the requirements from %s.\n"
            "Offline? Copy wheels over and set PIP_NO_INDEX=1 and PIP_FIND_LINKS=<wheel dir>." % REQUIREMENTS)
    still_missing = missing_requirements(python)
    if still_missing:
        die("Requirements still not satisfied after install: " + ", ".join(still_missing))


def main():
    if sys.version_info < MIN_PYTHON:
        die("Python %d.%d+ is required (this is %s)." % (MIN_PYTHON + (sys.version.split()[0],)))
    if not os.path.isfile(ENTRY_POINT):
        die("Cannot find %s." % ENTRY_POINT)

    python = active_env_python()
    if python:
        env_dir = sys.prefix if python == sys.executable else os.path.dirname(os.path.dirname(python))
        log("Using active environment: " + env_dir)
        if not has_pip(python):
            die("The active environment has no pip; install pip into it or deactivate it.")
    else:
        python = ensure_project_venv()

    ensure_requirements(python)

    # subprocess rather than os.exec*: exec on Windows doesn't replace the
    # process, which breaks console handling and exit codes.
    try:
        code = run([python, ENTRY_POINT] + sys.argv[1:])
    except KeyboardInterrupt:
        code = 130
    sys.exit(code)


if __name__ == "__main__":
    main()
