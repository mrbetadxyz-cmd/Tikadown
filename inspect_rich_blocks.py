import sys
sys.path.insert(0, '/home/ubuntu/softp4')
from aiogram.types import (
    InputRichBlockTable,
    InputRichBlockDetails,
    InputRichBlockSectionHeading,
    InputRichBlockDivider,
    InputRichBlockFooter,
    InputRichBlockParagraph,
)

print("InputRichBlockTable fields:", list(InputRichBlockTable.model_fields.keys()))
print("InputRichBlockDetails fields:", list(InputRichBlockDetails.model_fields.keys()))
print("InputRichBlockSectionHeading fields:", list(InputRichBlockSectionHeading.model_fields.keys()))
print("InputRichBlockDivider fields:", list(InputRichBlockDivider.model_fields.keys()))
print("InputRichBlockFooter fields:", list(InputRichBlockFooter.model_fields.keys()))
