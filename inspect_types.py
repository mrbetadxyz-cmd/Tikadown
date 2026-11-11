import sys
sys.path.insert(0, '/home/ubuntu/softp4')
from aiogram.types import RichBlockTableCell, InputRichBlockHeading, InputRichBlockDetails

print("Heading:", InputRichBlockHeading.model_fields)
print("TableCell:", RichBlockTableCell.model_fields)
print("Details:", InputRichBlockDetails.model_fields)
