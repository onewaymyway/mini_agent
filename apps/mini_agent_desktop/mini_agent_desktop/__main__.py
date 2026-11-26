from mini_agent_desktop.app import main
from mini_agent_desktop.bootstrap import guarded_main

if __name__ == "__main__":
    raise SystemExit(guarded_main(main))
