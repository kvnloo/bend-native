import Base

# double(n) must return n + n.
def double(n: Z) -> Z:
  match n:
    case 0n:
      0n
    case 1n+p:
      1n+double(p)
