import Base

# double(n) must return n + n.
def double(n: Nat) -> Nat: 
    if n == 0n:
        return 0n
    if n == 1n:
        return 2n
    return n + n
  match n:
    case 0n:
      0n
    case 1n+p:
      1n+double(p)
