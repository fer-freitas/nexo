package com.exemplo.api.domain;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.math.BigDecimal;
import org.junit.jupiter.api.Test;

class ParcelamentoTest {
  @Test
  void divideEmParcelasIguais() {
    assertEquals(new BigDecimal("33.33"), Parcelamento.parcela(new BigDecimal("100.00"), 3));
  }

  @Test
  void recusaMaisDeDozeParcelas() {
    assertThrows(IllegalArgumentException.class, () -> Parcelamento.parcela(BigDecimal.TEN, 13));
  }
}
