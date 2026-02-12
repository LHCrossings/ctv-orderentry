# Universal Rule: Line Numbers in Descriptions

## Principle
**If an IO (Insertion Order) contains explicit line numbers, those line numbers MUST be included in the Etere line descriptions.**

This ensures:
1. Easy reference back to the original IO
2. Clear communication with agencies about specific lines
3. Troubleshooting and verification
4. Invoice reconciliation

## Format
```
(Line #) <description>
```

Examples:
- Paid line: `(Line 1) CVC Chinese`
- Bonus line: `(Line 2) BNS CVC Chinese`
- With time/days: `(Line 5) M-Su 6a-11:59p Korean`
- ROS bonus: `(Line 8) BNS Filipino ROS`

## Implementation by Agency

### SAGENT ✅ IMPLEMENTED
- **Has line numbers**: YES - shown in "Line #" column
- **Format**: `(Line #) <Market> <Language>` or `(Line #) BNS <Market> <Language>`
- **Example**: `(Line 1) CVC Chinese`

### TCAA
- **Has line numbers**: NO - uses estimate numbers for tracking
- **Format**: `<Days> <Time> <Language>` or `BNS <Language> ROS`
- **No change needed**: Estimate-based tracking is sufficient

### Misfit
- **Has line numbers**: CHECK PDF - if yes, add line numbers
- **Current format**: TBD based on PDF structure

### WorldLink
- **Has line numbers**: CHECK PDF - if yes, add line numbers
- **Current format**: TBD based on PDF structure

### Daviselen
- **Has line numbers**: CHECK PDF - if yes, add line numbers
- **Current format**: TBD based on PDF structure

### Impact
- **Has line numbers**: CHECK PDF - if yes, add line numbers
- **Current format**: TBD based on PDF structure

### iGraphix
- **Has line numbers**: CHECK PDF - if yes, add line numbers
- **Current format**: TBD based on PDF structure

### Admerasia
- **Has line numbers**: CHECK PDF - if yes, add line numbers
- **Current format**: TBD based on PDF structure

### opAD
- **Has line numbers**: CHECK PDF - if yes, add line numbers
- **Current format**: TBD based on PDF structure

### RPM
- **Has line numbers**: CHECK PDF - if yes, add line numbers
- **Current format**: TBD based on PDF structure

### H&L Partners
- **Has line numbers**: CHECK PDF - if yes, add line numbers
- **Current format**: TBD based on PDF structure

## Implementation Checklist

For each parser that has line numbers:

1. **Parser dataclass**: Ensure line number is captured
   ```python
   @dataclass(frozen=True)
   class AgencyLine:
       line_number: int  # ✅ Must have this field
       # ... other fields
   ```

2. **Get description method**: Include line number prefix
   ```python
   def get_description(self) -> str:
       """Generate line description with line number."""
       base_desc = f"{self.market} {self.language}"
       
       if self.is_bonus():
           return f"(Line {self.line_number}) BNS {base_desc}"
       else:
           return f"(Line {self.line_number}) {base_desc}"
   ```

3. **Test output**: Verify line numbers appear
   ```
   ✅ (Line 1) CVC Chinese
   ✅ (Line 2) BNS CVC Chinese
   ❌ CVC Chinese  (missing line number!)
   ```

## Benefits

1. **Traceability**: Easy to reference specific lines when communicating with agencies
2. **Verification**: Quick cross-reference between Etere and original IO
3. **Error Resolution**: "There's an issue with Line 3" is clearer than "the second bonus line"
4. **Invoicing**: Match invoice line items back to original order
5. **Revisions**: Track which lines changed in order revisions

## When NOT to Include Line Numbers

- Agency doesn't use line numbers in their IOs
- Agency uses different tracking (estimate numbers, package codes, etc.)
- Multi-line splits in Etere (one IO line becomes multiple Etere lines)
  - In this case, only the first split should have the line number
  - Or all splits should have it: `(Line 1/1) ...`, `(Line 1/2) ...`

## Migration Path

For existing agencies:
1. Review sample IOs to confirm line numbers exist
2. Update parser to capture line_number field
3. Update get_description() method to include prefix
4. Test with sample PDFs
5. Document in agency-specific notes

## Universal Standard Going Forward

**All new agency integrations MUST check for line numbers and include them in descriptions if present.**

This is now part of the standard parser template and should be followed for consistency across all agencies.
