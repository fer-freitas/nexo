package com.exemplo.api.domain;

import java.math.BigDecimal;
import java.math.RoundingMode;

/** Regra de parcelamento sem juros (domínio em Java puro). */
public final class Parcelamento {
  private Parcelamento() {}

  public static BigDecimal parcela(BigDecimal valor, int parcelas) {
    if (parcelas < 1 || parcelas > 12) {
      throw new IllegalArgumentException("parcelas entre 1 e 12");
    }
    return valor.divide(BigDecimal.valueOf(parcelas), 2, RoundingMode.HALF_EVEN);
  }
}
