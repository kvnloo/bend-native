def qof(n: Type) -> Quant:
  &1

type A<n: Type, m: Type> is Kind(qof(n)):
  A1{a: A<m, n>}
