"""
Generate C0..CN with 5 methods:
- f is defined only in C0 (never overridden)
- g/h/i/j are overridden in every Ci (i>=1)

Fields:
- only every k-th class introduces one field (default k=50)
- C0 always has x0 so f can print it

Constructor args are written INLINE (one line).

Adds a large number of repeated calls to make caching meaningful.

Example:
  python gen_override_chain_sparse_fields.py --n 10000 --k 50 --reps 5000 --out override_5_N10000_k50.oop
"""
import argparse


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10000, help="Deepest class index (CN).")
    ap.add_argument("--k", type=int, default=50, help="Every k-th class adds a field.")
    ap.add_argument("--reps", type=int, default=5000, help="How many times to repeat the call block.")
    ap.add_argument("--out", default="override_5_N10000_k50.oop")
    args = ap.parse_args()

    N = args.n
    K = args.k
    reps = args.reps
    out = args.out

    # Which classes introduce a field
    field_classes = [i for i in range(N + 1) if i == 0 or (i % K == 0)]
    fields = [f"x{i}" for i in field_classes]
    ctor_args = ",".join(str(i) for i in range(len(fields)))  # 0..(num_fields-1)

    def write_call_block(fh):
        fh.write("call xb.f\n")
        fh.write("vcall xb.f\n")
        for m in ("g", "h", "i", "j"):
            fh.write(f"call xb.{m}\n")
            fh.write(f"vcall xb.{m}\n")

    with open(out, "w", encoding="utf-8") as fh:
        fh.write("; Auto-generated test (sparse fields, heavy call repetition)\n")
        fh.write(f"; Classes: C0..C{N}\n")
        fh.write(f"; Fields: C0 has x0, and every {K}-th class adds xi\n")
        fh.write("; Methods: f fixed in C0, g/h/i/j overridden in each derived class\n\n")

        # C0
        fh.write("CLASS C0\n")
        fh.write("base = None\n")
        fh.write("interfaces = []\n")
        fh.write("fields = [x0]\n")
        fh.write("methods = {\n")
        fh.write("  f -> [1000, x0]\n")
        fh.write("  g -> [0]\n")
        fh.write("  h -> [0]\n")
        fh.write("  i -> [0]\n")
        fh.write("  j -> [0]\n")
        fh.write("}\n\n")

        # C1..CN
        for i in range(1, N + 1):
            has_field = (i % K == 0)
            fh.write(f"CLASS C{i}\n")
            fh.write(f"base = C{i-1}\n")
            fh.write("interfaces = []\n")
            fh.write(f"fields = [x{i}]\n" if has_field else "fields = []\n")
            fh.write("methods = {\n")
            fh.write(f"  g -> [{i}]\n")
            fh.write(f"  h -> [{i}]\n")
            fh.write(f"  i -> [{i}]\n")
            fh.write(f"  j -> [{i}]\n")
            fh.write("}\n\n")

        # Instantiate leaf: args count equals total fields in chain
        fh.write(f"let x = new C{N}({ctor_args})\n")
        fh.write("let xb = cast<C0> x\n\n")

        fh.write(f"; Repeated call block x{reps} (10 calls per rep)\n")
        for _ in range(reps):
            write_call_block(fh)

    print(f"Wrote: {out}")
    print(f"Classes: {N+1}")
    print(f"Fields in leaf: {len(fields)} (field classes: {len(field_classes)})")
    print(f"Total calls written: {reps * 10}")


if __name__ == "__main__":
    main()
