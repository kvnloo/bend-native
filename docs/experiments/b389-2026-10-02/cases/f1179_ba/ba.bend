def qof(n: Type) -> Quant:
  &1

type B<m: Type> is Type:
  B1{a: A<m>}

type A<n: Type> is Kind(qof(n)):
  A1{b: B<n>}
