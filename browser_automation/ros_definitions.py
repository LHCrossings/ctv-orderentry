"""
Universal ROS (Run of Schedule) Definitions

ROS schedules define the standard time blocks for each language across
all Crossings TV and Asian Channel programming.

These schedules are used by ALL agencies when creating bonus ROS lines.
"""

# Standard ROS schedules for all languages
# Used by: TCAA, Misfit, WorldLink, opAD, RPM, H&L, Daviselen, etc.
ROS_SCHEDULES = {
    'Chinese': {
        'days': 'M-Su',
        'time': '6a-11:59p',
        'language': 'Chinese'
    },
    'Filipino': {
        'days': 'M-Su',
        'time': '4p-7p',
        'language': 'Filipino'
    },
    'Korean': {
        'days': 'M-Su',
        'time': '8a-10a',
        'language': 'Korean'
    },
    'Vietnamese': {
        'days': 'M-Su',
        'time': '11a-1p',
        'language': 'Vietnamese'
    },
    'Hmong': {
        'days': 'Sa-Su',
        'time': '6p-8p',
        'language': 'Hmong'
    },
    'South Asian': {
        'days': 'M-Su',
        'time': '1p-4p',
        'language': 'South Asian',
        'language_code': 'SA',
    },
    'Hindi': {
        'days': 'M-Su',
        'time': '1p-4p',
        'language': 'Hindi',
        'language_code': 'SA',
    },
    'Punjabi': {
        'days': 'M-F',
        'time': '2p-4p',
        'language': 'Punjabi',
        'language_code': 'P',
    },
    'Japanese': {
        'days': 'M-F',
        'time': '10a-11a',
        'language': 'Japanese'
    }
}


def get_ros_schedule(language: str) -> dict | None:
    """
    Get ROS schedule for a language.
    
    Args:
        language: Language name (e.g., "Chinese", "Filipino")
        
    Returns:
        Dictionary with 'days', 'time', 'language' or None if not found
        
    Examples:
        >>> get_ros_schedule('Chinese')
        {'days': 'M-Su', 'time': '6a-11:59p', 'language': 'Chinese'}
        
        >>> get_ros_schedule('Hmong')
        {'days': 'Sa-Su', 'time': '6p-8p', 'language': 'Hmong'}
    """
    return ROS_SCHEDULES.get(language)


def is_ros_schedule(days: str, time: str, language: str) -> bool:
    """
    Check if given days/time matches ROS schedule for language.
    
    Args:
        days: Day pattern (e.g., "M-Su")
        time: Time range (e.g., "6a-11:59p")
        language: Language name
        
    Returns:
        True if this matches the ROS schedule for this language
    """
    ros = get_ros_schedule(language)
    if not ros:
        return False
    
    return ros['days'] == days and ros['time'] == time
