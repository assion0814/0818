"""python -m aikube —— 模块入口。

用法分两类：
  python -m aikube <cli 子命令>           用户 CLI（cluster/node/get/run/...）
  python -m aikube <组件名> [组件参数]    进程编排拉起组件
      etcd / apiserver / scheduler / controller / kubelet
"""
import sys

COMPONENTS = {"etcd", "apiserver", "scheduler", "controller", "kubelet"}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in COMPONENTS:
        comp = argv.pop(0)
        if comp == "etcd":
            from .state import main as m
        elif comp == "apiserver":
            from .apiserver import main as m
        elif comp == "scheduler":
            from .scheduler import main as m
        elif comp == "controller":
            from .controller import main as m
        else:  # kubelet
            from .kubelet import main as m
        m(argv)
        return 0
    from .cli import main as cli_main
    return cli_main(argv) or 0


if __name__ == "__main__":
    sys.exit(main())
