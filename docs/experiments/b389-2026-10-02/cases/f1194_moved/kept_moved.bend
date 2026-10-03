import Base

def B(f: Nat, +c: Nat) -> Type:
  match f:
    case 0n: Empty
    case 1n+g: {c == c : Nat} & A(1n, g, c)

def A(e: Nat, f: Nat, +c: Nat) -> Type:
  match e:
    case 0n: Unit
    case 1n+p: B(f, c)
