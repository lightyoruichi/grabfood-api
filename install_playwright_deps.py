import sys
import os

# Add user packages to python path so we can import playwright
sys.path.insert(0, '/home/devopness/.local/lib/python3.11/site-packages')

# Remove PYTHONPATH from environment so subprocesses (like apt) do not inherit it
if 'PYTHONPATH' in os.environ:
    del os.environ['PYTHONPATH']

# Run playwright install-deps chromium
import playwright.__main__
sys.argv = ['playwright', 'install-deps', 'chromium']
playwright.__main__.main()
