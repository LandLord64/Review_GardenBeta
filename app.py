# Enhanced Features for ReviewGarden

# ==================== 1. IMPROVED STATE MANAGEMENT ====================
def init_session_state():
    """Initialize all session state variables in one place"""
    defaults = {
        "df_processed": None,
        "messages_generated": False,
        "campaign_sent": False,
        "current_step": 1,
        "test_mode": False,
        "campaign_results": None,
        "opt_out_list": set(),  # NEW: Track opt-outs
        "campaign_history": [],  # NEW: Store all campaigns
    }
    for key, default in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = default

# ==================== 2. ENHANCED MESSAGE GENERATION ====================
def generate_smart_message(business_name, customer_name, service_type="", 
                          service_date="", customer_tier="standard"):
    """
    Enhanced message generation with:
    - Customer tier awareness
    - Better personalization
    - More natural language
    """
    
    # Base templates by tier
    templates = {
        "vip": [
            "Hi {name}! As one of our valued customers at {business}, we'd love to hear about your recent {service}. Your feedback helps us maintain the exceptional service you deserve.",
            "{name}, thank you for choosing {business} for your {service}. We hope it exceeded your expectations! Would you share your experience?"
        ],
        "standard": [
            "Hi {name}! We hope you loved your {service} at {business}. Mind sharing a quick review?",
            "Hey {name}! Thanks for visiting {business}. How was your {service}? We'd appreciate your feedback!"
        ],
        "first_time": [
            "Welcome to {business}, {name}! We hope your first {service} was great. We'd love to hear what you think!",
            "Hi {name}! Thanks for trying {business}. Your first impression matters to us - would you leave a review?"
        ]
    }
    
    tier = customer_tier if customer_tier in templates else "standard"
    template = random.choice(templates[tier])
    
    # Smart substitution
    params = {
        "name": customer_name.split()[0],  # First name only
        "business": business_name,
        "service": service_type or "visit"
    }
    
    message = template.format(**params)
    
    # Add date context if recent (within 7 days)
    if service_date:
        try:
            date_obj = pd.to_datetime(service_date)
            days_ago = (datetime.now() - date_obj).days
            if days_ago <= 7:
                message += f" (from {days_ago} days ago)"
        except:
            pass
    
    return message

# ==================== 3. OPT-OUT MANAGEMENT ====================
def check_opt_out(phone):
    """Check if phone number has opted out"""
    return phone in st.session_state.opt_out_list

def add_opt_out(phone):
    """Add phone to opt-out list"""
    st.session_state.opt_out_list.add(phone)
    # In production: Save to database
    
def handle_incoming_sms(from_number, body):
    """
    Handle incoming SMS responses
    In production: Set up Twilio webhook
    """
    body_lower = body.lower().strip()
    
    if any(word in body_lower for word in ["stop", "unsubscribe", "opt out"]):
        add_opt_out(from_number)
        return "You've been unsubscribed. Reply START to resubscribe."
    
    if body_lower == "start":
        st.session_state.opt_out_list.discard(from_number)
        return "You're resubscribed to messages."
    
    return None  # Not a command

# ==================== 4. ENHANCED VALIDATION ====================
def validate_review_link(link):
    """Validate Google review link format"""
    if pd.isna(link) or not link:
        return False, "Missing review link"
    
    link_str = str(link).strip()
    
    # Check for valid Google review URL patterns
    valid_patterns = [
        "google.com/maps/place",
        "search.google.com/local/writereview",
        "g.page/",
        "maps.app.goo.gl"
    ]
    
    if not any(pattern in link_str for pattern in valid_patterns):
        return False, "Invalid Google review link"
    
    return True, link_str

def enhanced_csv_validation(df):
    """More comprehensive CSV validation"""
    issues = []
    warnings = []
    
    for idx, row in df.iterrows():
        row_num = idx + 2  # Excel row number
        
        # Phone validation
        is_valid, result = validate_phone_number(row['Phone'])
        if not is_valid:
            issues.append(f"Row {row_num}: Invalid phone - {result}")
        else:
            df.at[idx, 'Phone'] = result
            
            # Check opt-out list
            if check_opt_out(result):
                warnings.append(f"Row {row_num}: {row['Customer Name']} has opted out")
        
        # Review link validation
        is_valid, result = validate_review_link(row['Review Link'])
        if not is_valid:
            issues.append(f"Row {row_num}: {result}")
        
        # Email format check (warning only)
        email = str(row.get('Email', '')).strip()
        if email and '@' not in email:
            warnings.append(f"Row {row_num}: Suspicious email format")
        
        # Check for duplicate phones
        duplicates = df[df['Phone'] == row['Phone']]
        if len(duplicates) > 1:
            warnings.append(f"Row {row_num}: Duplicate phone number")
    
    return df, issues, warnings

# ==================== 5. ANALYTICS DASHBOARD ====================
def render_analytics():
    """Enhanced analytics dashboard"""
    st.header("📊 Campaign Analytics")
    
    if not st.session_state.campaign_history:
        st.info("No campaigns yet")
        return
    
    # Aggregate stats
    total_sent = sum(c['sent'] for c in st.session_state.campaign_history)
    total_failed = sum(c['failed'] for c in st.session_state.campaign_history)
    avg_success = (total_sent / (total_sent + total_failed) * 100) if total_sent + total_failed > 0 else 0
    
    # Metrics
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Campaigns", len(st.session_state.campaign_history))
    col2.metric("Messages Sent", total_sent)
    col3.metric("Avg Success Rate", f"{avg_success:.1f}%")
    col4.metric("Opt-outs", len(st.session_state.opt_out_list))
    
    # Campaign timeline
    st.subheader("Campaign History")
    history_df = pd.DataFrame([
        {
            "Date": c['timestamp'].strftime("%Y-%m-%d %H:%M"),
            "Sent": c['sent'],
            "Failed": c['failed'],
            "Success Rate": f"{(c['sent']/(c['sent']+c['failed'])*100):.1f}%" if c['sent']+c['failed'] > 0 else "0%",
            "Mode": "Test" if c.get('test_mode') else "Live"
        }
        for c in st.session_state.campaign_history
    ])
    st.dataframe(history_df, use_container_width=True)

# ==================== 6. CAMPAIGN SCHEDULER ====================
def schedule_campaign(df, send_time):
    """
    Schedule campaign for future sending
    In production: Use celery/background jobs
    """
    campaign_data = {
        'df': df,
        'scheduled_time': send_time,
        'status': 'scheduled',
        'created_at': datetime.now()
    }
    
    # Store in session state (in production: use database)
    if 'scheduled_campaigns' not in st.session_state:
        st.session_state.scheduled_campaigns = []
    
    st.session_state.scheduled_campaigns.append(campaign_data)
    return True

# ==================== 7. CUSTOMER SEGMENTATION ====================
def segment_customers(df):
    """Segment customers for targeted campaigns"""
    segments = {}
    
    # By service type
    if 'Service Type' in df.columns:
        for service_type in df['Service Type'].unique():
            if pd.notna(service_type):
                segments[f"Service: {service_type}"] = df[df['Service Type'] == service_type]
    
    # By recency (if service date available)
    if 'Service Date' in df.columns:
        try:
            df['Date_Parsed'] = pd.to_datetime(df['Service Date'], errors='coerce')
            df['Days_Ago'] = (datetime.now() - df['Date_Parsed']).dt.days
            
            segments["Recent (0-7 days)"] = df[df['Days_Ago'] <= 7]
            segments["This month (8-30 days)"] = df[(df['Days_Ago'] > 7) & (df['Days_Ago'] <= 30)]
            segments["Older (30+ days)"] = df[df['Days_Ago'] > 30]
        except:
            pass
    
    return segments

# ==================== 8. RATE LIMITING IMPROVEMENTS ====================
class RateLimiter:
    """Smart rate limiter with burst handling"""
    
    def __init__(self, max_per_hour=100, max_burst=10):
        self.max_per_hour = max_per_hour
        self.max_burst = max_burst
        self.sent_times = []
    
    def can_send(self):
        """Check if we can send based on rate limits"""
        now = datetime.now()
        hour_ago = now - pd.Timedelta(hours=1)
        
        # Clean old entries
        self.sent_times = [t for t in self.sent_times if t > hour_ago]
        
        # Check limits
        if len(self.sent_times) >= self.max_per_hour:
            return False, "Hourly limit reached"
        
        # Check burst (last 10 seconds)
        recent = [t for t in self.sent_times if t > now - pd.Timedelta(seconds=10)]
        if len(recent) >= self.max_burst:
            return False, "Burst limit reached"
        
        return True, None
    
    def record_sent(self):
        """Record a sent message"""
        self.sent_times.append(datetime.now())
    
    def get_wait_time(self):
        """Calculate how long to wait before next send"""
        if not self.sent_times:
            return 0
        
        now = datetime.now()
        hour_ago = now - pd.Timedelta(hours=1)
        recent_sends = [t for t in self.sent_times if t > hour_ago]
        
        if len(recent_sends) >= self.max_per_hour:
            oldest = min(recent_sends)
            wait = (oldest + pd.Timedelta(hours=1) - now).total_seconds()
            return max(0, wait)
        
        return 1  # Default delay

# ==================== 9. EXPORT ENHANCEMENTS ====================
def export_campaign_report(df, campaign_results):
    """Generate comprehensive campaign report"""
    report = {
        "summary": {
            "timestamp": campaign_results['timestamp'].isoformat(),
            "total_recipients": len(df),
            "sent": campaign_results['sent'],
            "failed": campaign_results['failed'],
            "skipped": campaign_results['skipped'],
            "success_rate": f"{(campaign_results['sent']/(campaign_results['sent']+campaign_results['failed'])*100):.2f}%"
        },
        "failures": df[df['SMS_Status'].str.contains('Failed', na=False)][
            ['Customer Name', 'Phone', 'Error']
        ].to_dict('records'),
        "opt_outs": list(st.session_state.opt_out_list)
    }
    
    return report

# ==================== 10. UI IMPROVEMENTS ====================
def render_progress_with_eta(current, total, start_time):
    """Show progress bar with ETA"""
    elapsed = (datetime.now() - start_time).total_seconds()
    rate = current / elapsed if elapsed > 0 else 0
    remaining = total - current
    eta_seconds = remaining / rate if rate > 0 else 0
    
    progress = current / total
    eta_str = f"{int(eta_seconds // 60)}m {int(eta_seconds % 60)}s"
    
    st.progress(progress)
    col1, col2, col3 = st.columns(3)
    col1.metric("Progress", f"{current}/{total}")
    col2.metric("Rate", f"{rate:.1f}/sec")
    col3.metric("ETA", eta_str)
