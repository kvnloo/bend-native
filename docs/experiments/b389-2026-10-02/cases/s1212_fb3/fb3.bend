import Base
import ./a-b.bend as AB

def a_2d_b.M(n: Nat) -> Bool:
  AB.ign(AB.L(n), False{})

def AB.L(n):
  match n:
    case Zero{}:
      True{}
    case Succ{p}:
      AB.M(p)

def main() -> Bool:
  a_2d_b.M(Zero{})
