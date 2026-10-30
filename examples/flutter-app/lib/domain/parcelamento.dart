/// Regra de parcelamento sem juros (domínio em Dart puro).
double parcela(double valor, int parcelas) {
  if (parcelas < 1 || parcelas > 12) {
    throw ArgumentError.value(parcelas, 'parcelas', 'entre 1 e 12');
  }
  return (valor / parcelas * 100).roundToDouble() / 100;
}
