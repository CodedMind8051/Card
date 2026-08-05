PROMPT = (
    "Extract all information from this image and return ONLY a valid JSON object "
    "with these keys: school_name, student_name, father_name, mother_name, class, "
    "section, roll_number, mobile_number, dob, address, student_photo_bbox. "
    "For student_photo_bbox, return a list of 4 decimal numbers between 0.0 and 1.0 "
    "representing [x_ratio, y_ratio, width_ratio, height_ratio] relative to the "
    "total image width and height. Example: [0.15, 0.20, 0.10, 0.15]. "
    "Missing fields=\"\". No extra text. And if information in hindi then convert into english.and if the address is too long just give the important part of the address. Return only the JSON object, nothing else."
)