"""AnsiWEB command line. After installation, run it as: sudo ansiweb <command>

  init                   create data folders, keys and default configuration
  set-password [user]    set or reset a web password (creates the first admin)
  add-user <name> <role> add a web user (roles: admin, operator, helpdesk, viewer)
  list-users             show the web users and their roles
  update-cache           check vendors and refresh the cached installers
  deploy [target]        deploy apps (target: all, site:HQ, group:finance, pc:PC-HQ-001)
  plan                   rebuild the inventory and deployment plan

Without the wrapper: python -m ansiweb.cli <command>, with ANSIWEB_DATA set.
"""
import getpass
import sys

from . import cache, jobs, paths, plan, store, users, vault


def _ask_password(user: str) -> str:
    while True:
        pw = getpass.getpass(f"Password for web user '{user}': ")
        try:
            users.check_password_rules(pw)
        except users.UserError as exc:
            print(exc)
            continue
        if pw != getpass.getpass("Repeat password: "):
            print("Passwords do not match.")
            continue
        return pw


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

    users.migrate()

    if cmd == "list-users":
        for u in users.all_users():
            print(f"{u['username']:24} {users.ROLES[u['role']]['label']:16}"
                  f"{' (disabled)' if u.get('disabled') else ''}")
        if not users.any_users():
            print("No users yet. Create one with: ansiweb set-password admin")
        return 0

    if cmd == "add-user":
        if len(args) < 2:
            print("usage: add-user <name> <role>")
            return 1
        try:
            users.validate_username(args[0])
            pw = _ask_password(args[0])
            # Typed at the console by whoever will use it
            users.create(args[0], pw, args[1], force_change=False)
        except users.UserError as exc:
            print(exc)
            return 1
        print(f"Added {args[0]} as {users.ROLES[args[1]]['label']}.")
        return 0

    if cmd == "set-password":
        user = args[0] if args else (users.all_users()[0]["username"] if users.any_users() else "admin")
        pw = _ask_password(user)
        if users.get(user):
            users.set_password(user, pw)
            print(f"Password for '{user}' updated.")
        else:
            users.create(user, pw, "admin", force_change=False)
            print(f"Created administrator '{user}'.")
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
