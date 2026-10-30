import 'package:flutter_app/domain/parcelamento.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('divide em parcelas iguais', () => expect(parcela(100, 3), 33.33));
  test('recusa mais de 12 parcelas', () => expect(() => parcela(100, 13), throwsArgumentError));
}
