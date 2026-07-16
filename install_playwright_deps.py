import sys
import os

# Add user packages to python path so we can import playwright
sys.path.insert(0, '/home/devopness/.local/lib/python3.11/site-packages')

# Remove PYTHONPATH from environment so subprocesses (like apt) do not inherit it
if 'PYTHONPATH' in os.environ:
    del os.environ['PYTHONPATH']

# Disable the broken command-not-found apt hook to prevent apt-get from crashing with ModuleNotFoundError: No module named 'apt_pkg'
import subprocess
try:
    subprocess.run(['rm', '-f', '/etc/apt/apt.conf.d/50command-not-found'], check=True)
    print("Disabled command-not-found APT hook to bypass apt_pkg error.")
except Exception as e:
    print(f"Failed to remove command-not-found hook (non-fatal): {e}")

# Run playwright install-deps chromium
import playwright.__main__
sys.argv = ['playwright', 'install-deps', 'chromium']
playwright.__main__.main()
