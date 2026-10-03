import Base

# double(n) must return n + n.
1|def double(n: Nat) -> Nat: if n == 0n: return 0n else: return 1n + double(n - 1n)
  match n:
    case 0n:
      0n
    case 1n+p:
      1n+double(p)
