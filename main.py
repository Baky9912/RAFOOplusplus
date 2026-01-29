
import os
import sys
from parser import Parser
from interpreter import Interpreter, Config


def main():
    # Usage:
    #   python -m main program.oop
    #
    # Optional env toggles:
    #   RAF_METRICS=1  -> prints resolve/cache/dispatch metrics
    #   RAF_GC=1       -> enables GC (manual "gc" statement)
    #   RAF_VTABLE=1   -> enables vtable/itable fast dynamic dispatch (for vcall)

    if len(sys.argv) != 2:
        print("Usage: python -m main <program-file>")
        return

    filename = sys.argv[1]
    try:
        with open(filename, "r", encoding="utf-8") as f:
            program = f.read()
    except FileNotFoundError:
        print(f"Error: file '{filename}' not found.")
        return

    parser = Parser(program)
    classes, statements = parser.parse()

    cfg = Config(
        enable_metrics=(os.getenv("RAF_METRICS") == "1"),
        enable_gc=(os.getenv("RAF_GC") == "1"),
        enable_vtable=(os.getenv("RAF_VTABLE") == "1"),
    )

    interp = Interpreter(classes, statements, config=cfg)
    interp.run()

    # Debug structure dumps (optional):
    interp.print_classes()
    interp.print_instances()


if __name__ == "__main__":
    main()
