from src.domain.enums import OrderType, Language
from src.domain.value_objects import TimeRange

# No more magic strings!
print(f"WorldLink needs refresh: {OrderType.WORLDLINK.requires_block_refresh()}")
print(f"TCAA needs refresh: {OrderType.TCAA.requires_block_refresh()}")

# Parse time ranges
tr = TimeRange.from_string("5p-7p")
print(f"Time range in Etere format: {tr.to_etere_format()}")

# Language schedules
days, times = Language.MANDARIN.get_ros_schedule()
print(f"Chinese ROS: {days} {times}")
print(f"Chinese block code: {Language.MANDARIN.get_block_abbreviation()}")

print("\n✅ Domain layer working perfectly!")