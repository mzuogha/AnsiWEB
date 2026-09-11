"""AnsiWEB command line.

  python -m ansiweb.cli init                   create data folders, keys and default config
  python -m ansiweb.cli set-password [user]    set the web admin login
  python -m ansiweb.cli update-cache           check vendors and refresh cached installers
  python -m ansiweb.cli deploy [target]        deploy apps (target: all, site:HQ, group:x, pc:NAME)
  python -m ansiweb.cli plan                   rebuild inventory and deployment plan
"""
import getpass
import sys

from werkzeug.security import generate_password_hash

from . import cache, jobs, paths, plan, store, vault


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, args = argv[0], argv[1:]

    paths.ensure_dirs()
    vault.ensure_vault_pass()
    vault.ensure_ansible_vault()
    jobs.init_db()

    if cmd == "init":
        store.load()
        store.regenerate_all()
        print(f"AnsiWEB data folder ready: {paths.DATA_DIR}")
        return 0

    if cmd == "set-password":
        user = args[0] if args else (vault.admin_record().get("username") or "admin")
        while True:
            pw = getpass.getpass(f"New password for web user '{user}': ")
            if len(pw) < 10:
                print("Use at least 10 characters.")
                continue
            if pw != getpass.getpass("Repeat password: "):
                print("Passwords do not match.")
                continue
            break
        vault.set_admin(user, generate_password_hash(pw))
        print("Password saved.")
        return 0

    if cmd == "update-cache":
        counts = cache.update_all(print)
        return 0 if counts["error"] == 0 else 2

    if cmd == "deploy":
        store.regenerate_all()
        return jobs.run_command(jobs.playbook_cmd("deploy.yml", store.limit_for(args[0] if args else "all")), print)

    if cmd == "plan":
        store.regenerate_all()
        p = plan.write_plan()
        print(f"{len(p['apps'])} app(s) ready, {len(p['skipped'])} skipped, {len(p['hosts'])} PC(s)")
        return 0

    print(f"Unknown command '{cmd}'\n{__doc__}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
