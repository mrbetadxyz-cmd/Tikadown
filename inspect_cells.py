import sys
sys.path.insert(0, '/home/ubuntu/softp4')
import inspect
from aiogram.types import InputRichBlockTable, InputRichBlockDetails

print("InputRichBlockTable cells annotation:", InputRichBlockTable.model_fields['cells'].annotation)
print("InputRichBlockDetails summary annotation:", InputRichBlockDetails.model_fields['summary'].annotation)
print("InputRichBlockDetails blocks annotation:", InputRichBlockDetails.model_fields['blocks'].annotation)
