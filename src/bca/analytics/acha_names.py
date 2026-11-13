"""The ACHA ranking PDFs' short team names that whole-word matching can't place.

Each maps a PDF name to words that appear in our ``team_names`` value. Every
other PDF name is matched by its words ("Arizona State" -> "MD1 Arizona State
University"); a name that matches nothing is an error, never skipped.
"""

PDF_ALIASES: dict[str, str] = {
    "Purdue Northwest": "Purdue University Northwest",
    "Rochester Inst of Tech": "Rochester Institute of Technology",
    "Indiana Pennsylvania": "Indiana University of Pennsylvania",
    "Navy": "United States Naval Academy",
    "Nevada-Las Vegas": "Nevada, Las Vegas",
    "New York": "New York University",
    "New Jersey": "College of New Jersey",
}
