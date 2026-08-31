#!/usr/bin/env python3
"""Entry point. Locally: reads secrets from a .env file. In CI: from the env."""
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from tvtracker.run import main

if __name__ == "__main__":
    main()
