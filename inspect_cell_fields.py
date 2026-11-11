import sys
sys.path.insert(0, '/home/ubuntu/softp4')
from aiogram.types import RichBlockTableCell

print("RichBlockTableCell fields:", list(RichBlockTableCell.model_fields.keys()))
