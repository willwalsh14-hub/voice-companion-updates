"""Keep expected native stderr away from Windows PowerShell 5.1 error handling."""
import argparse
import subprocess


def ensure_login(gh, runner=subprocess.run):
    status = runner([str(gh), 'auth', 'status', '--hostname', 'github.com'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if status.returncode == 0:
        print('GitHub sign-in is ready for automatic publishing.', flush=True)
        return 0
    print('Sign in to GitHub. Follow the one-time code and browser instructions below. Keep this window open.', flush=True)
    # gh writes its device code to stderr. Merge it into stdout so PowerShell
    # displays it rather than treating normal login guidance as a fatal error.
    login = runner([str(gh), 'auth', 'login', '--hostname', 'github.com', '--git-protocol', 'https', '--web'], stderr=subprocess.STDOUT)
    if login.returncode:
        print('GitHub sign-in did not finish. Run the installer builder again to retry.', flush=True)
        return 1
    verified = runner([str(gh), 'auth', 'status', '--hostname', 'github.com'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if verified.returncode:
        print('GitHub sign-in could not be verified. Run the installer builder again to retry.', flush=True)
        return 1
    print('GitHub sign-in completed. Continuing automatically.', flush=True)
    return 0


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--gh', required=True)
    args=parser.parse_args()
    try: return ensure_login(args.gh)
    except OSError:
        print('GitHub CLI could not start. Check its installation, then run the installer builder again.')
        return 1

if __name__ == '__main__': raise SystemExit(main())
