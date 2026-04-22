"""
Модуль для загрузки и работы с маппингом discountPlanId -> описание из Excel-файла.
"""
import os
import openpyxl
from typing import Dict, Optional


class DiscountMapper:
    """Класс для работы с маппингом discount plan ID к их описаниям."""
    
    def __init__(self, excel_path: str = "DISPID.xlsx"):
        """
        Инициализация маппера.
        
        Args:
            excel_path: Путь к Excel-файлу с маппингом
        """
        self.excel_path = excel_path
        self._mapping: Dict[int, str] = {}
        self._load_mapping()
    
    def _load_mapping(self):
        """Загрузить маппинг из Excel-файла."""
        # Попробовать оригинальный путь, затем варианты регистра
        base_dir = os.path.dirname(os.path.abspath(__file__))
        candidates = [
            self.excel_path,
            os.path.join(base_dir, self.excel_path),
            os.path.join(base_dir, "dispid.xlsx"),
            os.path.join(base_dir, "DISPID.xlsx"),
            os.path.join(base_dir, "Dispid.xlsx"),
        ]
        resolved = None
        for c in candidates:
            if os.path.exists(c):
                resolved = c
                break
        if not resolved:
            print(f"⚠️  Файл {self.excel_path} не найден. Маппинг discountPlanId недоступен.")
            return
        self.excel_path = resolved
        
        try:
            wb = openpyxl.load_workbook(self.excel_path, read_only=True, data_only=True)
            ws = wb.active
            
            # Пропускаем заголовок (первая строка)
            for row in ws.iter_rows(min_row=2, values_only=True):
                if row and len(row) >= 2:
                    dcpl_id = row[0]  # Column A: DCPL_ID
                    description = row[1]  # Column B: DEF
                    
                    # Преобразуем в int, если возможно
                    if dcpl_id is not None:
                        try:
                            dcpl_id = int(dcpl_id)
                            if description:
                                # Очищаем описание от лишних пробелов и неразрывных пробелов
                                description = str(description).replace('\xa0', ' ').strip()
                                self._mapping[dcpl_id] = description
                        except (ValueError, TypeError):
                            continue
            
            wb.close()
            print(f"✅ Загружено {len(self._mapping)} маппингов discountPlanId из {self.excel_path}")
        
        except Exception as e:
            print(f"⚠️  Ошибка при загрузке {self.excel_path}: {e}")
    
    def get_description(self, discount_plan_id: int) -> Optional[str]:
        """
        Получить описание по discountPlanId.
        
        Args:
            discount_plan_id: ID плана скидки
            
        Returns:
            Описание скидки или None, если не найдено
        """
        return self._mapping.get(discount_plan_id)
    
    def get_all_mappings(self) -> Dict[int, str]:
        """
        Получить все маппинги.
        
        Returns:
            Словарь {discountPlanId: описание}
        """
        return self._mapping.copy()
    
    def is_loaded(self) -> bool:
        """Проверить, загружены ли данные."""
        return len(self._mapping) > 0


# Глобальный экземпляр для использования в других модулях
_global_mapper: Optional[DiscountMapper] = None


def get_discount_mapper() -> DiscountMapper:
    """
    Получить глобальный экземпляр DiscountMapper (singleton).
    
    Returns:
        Единственный экземпляр DiscountMapper
    """
    global _global_mapper
    if _global_mapper is None:
        _global_mapper = DiscountMapper()
    return _global_mapper


def get_discount_description(discount_plan_id: int) -> str:
    """
    Удобная функция для получения описания по ID.
    
    Args:
        discount_plan_id: ID плана скидки
        
    Returns:
        Описание скидки или пустая строка, если не найдено
    """
    mapper = get_discount_mapper()
    description = mapper.get_description(discount_plan_id)
    return description if description else ""


if __name__ == "__main__":
    # Тестирование модуля
    print("=" * 60)
    print("  Тестирование DiscountMapper")
    print("=" * 60)
    
    mapper = DiscountMapper()
    
    if mapper.is_loaded():
        print(f"\n✅ Успешно загружено {len(mapper.get_all_mappings())} записей\n")
        
        # Показать несколько примеров
        test_ids = [17, 100, 101, 654, 281, 999999]
        print("Примеры маппингов:")
        for disc_id in test_ids:
            desc = mapper.get_description(disc_id)
            if desc:
                print(f"  {disc_id:6d} -> {desc}")
            else:
                print(f"  {disc_id:6d} -> [НЕ НАЙДЕН]")
    else:
        print("\n❌ Не удалось загрузить маппинги")
