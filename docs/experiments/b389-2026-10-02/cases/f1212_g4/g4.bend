import Base
def keep(-p: Nat, x: Nat) -> Nat:
  x
law L:
  for n: Nat
  Nat
law L2:
  for n: Nat
  Nat
def h(n: Nat) -> Nat:
  keep(L(n), n)
def L(n):
  match n:
    case Zero{}:
      keep(L2(0n), 0n)
    case Succ{m}:
      h(m)
def L2(n):
  0n
