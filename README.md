# Resume_Parser_Project
### Resume Parser Project

## Overview

The Resume Parser Project is a comprehensive system designed to streamline the recruitment process by automating resume parsing, analysis, and evaluation. The system combines traditional rule-based parsing with advanced AI-powered analysis to extract structured information from various resume formats (PDF, DOCX, TXT, CSV) and provides intelligent search, ranking, and skill gap analysis capabilities.





## Features

### Intelligent Resume Parsing

- **Multi-format Support**: Parse resumes in PDF, DOCX, TXT, and CSV formats
- **Dual-approach Parsing**: Combines AI (Google Gemini) and rule-based extraction for reliability
- **Structured Data Extraction**: Extracts personal info, education, experience, skills, and more
- **Fallback Mechanisms**: Multiple extraction methods ensure reliable parsing


### Smart Search and Ranking

- **Skill-based Search**: Find candidates with specific skills or related competencies
- **Sophisticated Scoring**: Algorithm considers multiple factors for ranking:

- Skill match (up to 40 points)
- Relevant work experience (up to 30 points)
- Relevant projects (up to 15 points)
- Relevant education (up to 10 points)
- Certifications (up to 5 points)



- **Partial Matching**: Recognizes similar skills and partial matches intelligently
- **Visual Ranking**: Badges and score indicators show match quality


### Skill Gap Analysis

- **Predefined Job Roles**: Standard skill requirements for common positions
- **Custom Analysis**: Support for specific job requirements
- **Visual Representation**: Clear display of matching vs. missing skills
- **Course Recommendations**: Personalized learning suggestions for skill development
- **Match Percentage**: Calculation with color-coded indicators


### LinkedIn and GitHub Integration

- **URL Extraction**: Advanced parsing logic to identify and extract profile links
- **Validation**: URL validation and formatting for consistency
- **Structured Storage**: Links saved alongside other candidate details
- **Portfolio Access**: Quick access to professional profiles and code repositories


### Application Form Auto-fill

- **One-click Auto-fill**: Automatically populate application forms with parsed resume data
- **Dynamic Form Fields**: Add multiple education and work experience entries
- **Real-time Validation**: Ensure data accuracy and completeness
- **Responsive Design**: Works seamlessly across all devices


## Technologies Used

### Backend

- **Flask**: Web framework for handling HTTP requests and routing
- **MongoDB**: Database for storing parsed resume data
- **Google Gemini AI**: Advanced AI model for enhanced resume parsing
- **PyPDF2/pdfplumber**: Libraries for PDF text extraction
- **python-docx**: Library for DOCX parsing
- **Regular expressions**: Pattern matching for data extraction


### Frontend

- **HTML/CSS**: Structure and styling for web pages
- **JavaScript**: Client-side interactivity and AJAX requests
- **Chart.js**: Interactive data visualization for dashboard
- **Font Awesome**: Icon library for improved UI
- **Responsive design**: Mobile-friendly interface


## Installation and Setup

### Prerequisites

- Python 3.8 or higher
- MongoDB (or use the in-memory fallback option)
- Google Gemini API key (for AI-enhanced parsing)


### Installation Steps

1. Clone the repository


```shellscript
git clone https://github.com/yourusername/resume-parser-project.git
cd resume-parser-project
```

2. Create and activate a virtual environment


```shellscript
python -m venv venv
# On Windows
venv\Scripts\activate
# On macOS/Linux
source venv/bin/activate
```

3. Install dependencies


```shellscript
pip install -r requirements.txt
```

4. Set up environment variables


```shellscript
# Create a .env file with the following variables
MONGODB_URI=your_mongodb_connection_string
GEMINI_API_KEY=your_gemini_api_key
```

5. Run the application


```shellscript
python app.py
```

6. Access the application at `http://localhost:5000`


## Usage

### Home/Upload Page

1. Navigate to the home page
2. Upload a resume file (PDF, DOCX, TXT, or CSV)
3. Wait for the parsing process to complete
4. Review the extracted information
5. Edit any incorrectly parsed information if necessary
6. Navigate to other features using the provided buttons


### Dashboard

1. View statistics and charts showing resume data distribution
2. Use the search functionality to find candidates with specific skills
3. Review the ranked results based on the intelligent scoring system
4. Click on a resume to view detailed information


### Application Form

1. Select a parsed resume to auto-fill the form
2. Add or remove education and work experience entries as needed
3. Complete any missing information
4. Submit the form


### Skill Gap Analysis

1. Select a job role or define custom skill requirements
2. View the comparison between candidate skills and job requirements
3. Review the missing skills and course recommendations
4. Generate a report for sharing
5. ## Applications of GenAI

The project leverages Google Gemini AI for several advanced features:

1. **Smart Name and Entity Extraction**: Recognizes named entities like names, companies, and job titles from unstructured text.
2. **Skills Categorization**: Uses semantic understanding to identify and categorize technical and soft skills by understanding context.
3. **Role Recommendation**: Analyzes candidate skills to suggest potential job roles based on industry standards and market demands.
4. **Resume Scoring**: Evaluates resumes beyond keyword matches by analyzing contextual relevance of skills to experience.


## Future Improvements

- **Enhanced AI Parsing**: Integrate more advanced AI models for improved accuracy
- **Multi-language Support**: Add support for resumes in different languages
- **Candidate Comparison**: Implement side-by-side comparison of multiple candidates
- **Interview Question Generator**: Create tailored interview questions based on resume content
- **ATS Integration**: Develop connectors for popular Applicant Tracking Systems
- **Mobile App**: Create a mobile application for on-the-go resume parsing and evaluation.
- AI PROJECT/
│
├── app.py                      # Main Flask application file with all routes and parsing logic
│
├── dist/                       # Distribution directory for compiled/built files
│
├── CVs/                        # Directory to store uploaded resume files
│
├── templates/                  # HTML templates for Flask
│   ├── index.html              # Home/Upload page
│   ├── dashboard.html          # Dashboard page with statistics and search
│   ├── form.html               # Application form with auto-fill
│   └── skill_gap.html          # Skill gap analysis page

││
├── venv/                       # Python virtual environment
│   ├── Include/                # C headers for packages
│   ├── Lib/                    # Python packages installed in the virtual environment
│   ├── Scripts/                # Executable scripts including Python interpreter
│   └── pyvenv.cfg              # Virtual environment configuration
