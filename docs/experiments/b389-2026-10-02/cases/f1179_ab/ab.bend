def qof(n: Type) -> Quant:
  &1

type A<n: Type> is Kind(qof(n)):
  A1{b: B<n>}

type B<m: Type> is Type:
  B1{a: A<m>}
