import Base

# double(n) must return n + n.
def double(n: Nat) -> Nat:
  match n:
    case 0n:
      0n
    case 1n+p -> 2n+p:
      1n+double(p)
