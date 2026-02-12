"""
Universal Language Utilities

Language-specific utilities used across all agencies for:
- Block prefix mapping (for Etere programming block filtering)
- Language name normalization
- Language detection from program names

Used by: TCAA, Misfit, WorldLink, opAD, RPM, H&L, Daviselen, etc.
"""

from typing import Optional


# Universal language to block prefix mapping
# These prefixes are used in Etere to filter programming blocks
LANGUAGE_BLOCK_PREFIXES = {
    'Chinese': ['C', 'M'],      # Both Cantonese and Mandarin
    'Cantonese': ['C'],
    'Mandarin': ['M'],
    'Filipino': ['T'],
    'Korean': ['K'],
    'Vietnamese': ['V'],
    'Hmong': ['Hm'],
    'South Asian': ['SA', 'P'], # Both Hindi (SA) and Punjabi (P)
    'Hindi': ['SA'],
    'Punjabi': ['P'],
    'Japanese': ['J'],
}


def get_language_block_prefixes(
    language: str,
    hindi_punjabi_both: Optional[str] = None
) -> list[str]:
    """
    Get Etere block prefixes for a given language.
    
    This is the universal function used by ALL agencies to determine
    which programming blocks to select in Etere for a given language.
    
    Args:
        language: Language name (e.g., "Chinese", "Korean", "South Asian")
        hindi_punjabi_both: For South Asian, specify "Hindi", "Punjabi", or "Both"
        
    Returns:
        List of block prefixes (e.g., ["M"], ["C", "M"], ["SA", "P"])
        
    Examples:
        >>> get_language_block_prefixes('Chinese')
        ['C', 'M']
        
        >>> get_language_block_prefixes('Korean')
        ['K']
        
        >>> get_language_block_prefixes('South Asian', 'Hindi')
        ['SA']
        
        >>> get_language_block_prefixes('South Asian', 'Both')
        ['SA', 'P']
    """
    # Handle South Asian disambiguation
    if language.lower() == "south asian":
        if hindi_punjabi_both:
            choice = hindi_punjabi_both.lower()
            if choice == "hindi":
                return ["SA"]
            elif choice == "punjabi":
                return ["P"]
            else:  # both
                return ["SA", "P"]
        else:
            return ["SA", "P"]  # Default to both
    
    # Standard language mapping (case-insensitive lookup)
    language_lower = language.lower()
    for lang_name, prefixes in LANGUAGE_BLOCK_PREFIXES.items():
        if lang_name.lower() == language_lower:
            return prefixes
    
    # Not found - return empty list
    return []


def normalize_language_name(language: str) -> str:
    """
    Normalize language name to standard form.
    
    Handles variations like:
    - "chinese" → "Chinese"
    - "KOREAN" → "Korean"
    - "south asian" → "South Asian"
    
    Args:
        language: Language name in any case
        
    Returns:
        Normalized language name or original if not recognized
    """
    language_lower = language.lower()
    
    # Map to standard names
    for standard_name in LANGUAGE_BLOCK_PREFIXES.keys():
        if standard_name.lower() == language_lower:
            return standard_name
    
    # Not found - return title case
    return language.title()


def extract_language_from_program(program: str) -> str:
    """
    Extract language name from program description.
    
    Looks for language keywords in program text.
    
    Args:
        program: Program description (e.g., "Cantonese News", "Korean Entertainment")
        
    Returns:
        Detected language name or "Unknown"
        
    Examples:
        >>> extract_language_from_program("Cantonese News")
        'Cantonese'
        
        >>> extract_language_from_program("Filipino Talk Show")
        'Filipino'
    """
    program_lower = program.lower()
    
    # Check each language (order matters - check "South Asian" before "Asian")
    language_keywords = [
        ('South Asian', ['south asian', 'hindi', 'punjabi']),
        ('Cantonese', ['cantonese']),
        ('Mandarin', ['mandarin']),
        ('Chinese', ['chinese']),
        ('Filipino', ['filipino', 'tagalog']),
        ('Korean', ['korean']),
        ('Vietnamese', ['vietnamese']),
        ('Hmong', ['hmong']),
        ('Japanese', ['japanese']),
    ]
    
    for language, keywords in language_keywords:
        if any(keyword in program_lower for keyword in keywords):
            return language
    
    return "Unknown"


def is_south_asian_language(language: str) -> bool:
    """
    Check if language is South Asian (requires Hindi/Punjabi disambiguation).
    
    Args:
        language: Language name
        
    Returns:
        True if this is South Asian language requiring disambiguation
    """
    return language.lower() in ['south asian', 'hindi', 'punjabi']
