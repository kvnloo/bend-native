import Base

def B(-c: Nat, f: Nat) -> Type:
  match f:
    case 0n: Empty
    case 1n+g: {c == c : Nat} & A(c, 1n, g)

def A(-c: Nat, e: Nat, f: Nat) -> Type:
  match e:
    case 0n: Unit
    case 1n+p: B(c, f)
