from django import forms



from .models import (

    Video,

    YouTubeCredential,

    InstagramCredential,

    FacebookCredential,

    XCredential,

)





class VideoUploadForm(forms.ModelForm):



    # ============================================================

    # AUTO SYSTEM PLATFORM SELECTION

    # ============================================================



    AUTO_PLATFORM_CHOICES = [

        ('youtube', 'YouTube'),

        ('instagram', 'Instagram'),

        ('facebook', 'Facebook'),

        ('x', 'X'),

    ]



    auto_platforms = forms.MultipleChoiceField(

        choices=AUTO_PLATFORM_CHOICES,

        required=False,

        widget=forms.CheckboxSelectMultiple(),

        label='Auto Publish Platforms',

    )



    class Meta:

        model = Video



        fields = [

            'video_file',

            'youtube_url',



            # Workflow

            'workflow_mode',



            # Auto system accounts

            'selected_youtube_channel',

            'selected_instagram_account',

            'selected_facebook_page',

            'selected_x_account',



            # Processing

            'processing_mode',

            'output_format',

        ]



        widgets = {



            # ====================================================

            # VIDEO SOURCE

            # ====================================================



            'video_file': forms.ClearableFileInput(

                attrs={

                    'accept': 'video/*',

                }

            ),



            'youtube_url': forms.URLInput(

                attrs={

                    'placeholder': 'Enter YouTube video URL',

                    'autocomplete': 'off',

                }

            ),



            # ====================================================

            # WORKFLOW

            # ====================================================



            'workflow_mode': forms.RadioSelect(),



            # ====================================================

            # AUTO ACCOUNT SELECTION

            # ====================================================



            'selected_youtube_channel': forms.Select(),



            'selected_instagram_account': forms.Select(),



            'selected_facebook_page': forms.Select(),



            'selected_x_account': forms.Select(),



            # ====================================================

            # PROCESSING

            # ====================================================



            'processing_mode': forms.Select(),



            'output_format': forms.Select(),

        }



    def __init__(self, *args, **kwargs):

        super().__init__(*args, **kwargs)



        # ========================================================

        # WORKFLOW

        # ========================================================



        self.fields['workflow_mode'].choices = [

            ('auto', 'Auto System'),

            ('manual', 'Manual System'),

        ]



        self.fields['workflow_mode'].required = True



        # ========================================================

        # YOUTUBE

        # ========================================================



        self.fields[

            'selected_youtube_channel'

        ].queryset = YouTubeCredential.objects.filter(

            is_active=True

        )



        self.fields[

            'selected_youtube_channel'

        ].required = False



        self.fields[

            'selected_youtube_channel'

        ].empty_label = 'Select YouTube Channel'



        # ========================================================

        # INSTAGRAM

        # ========================================================



        self.fields[

            'selected_instagram_account'

        ].queryset = InstagramCredential.objects.filter(

            is_active=True

        )



        self.fields[

            'selected_instagram_account'

        ].required = False



        self.fields[

            'selected_instagram_account'

        ].empty_label = 'Select Instagram Account'



        # ========================================================

        # FACEBOOK

        # ========================================================



        self.fields[

            'selected_facebook_page'

        ].queryset = FacebookCredential.objects.filter(

            is_active=True

        )



        self.fields[

            'selected_facebook_page'

        ].required = False



        self.fields[

            'selected_facebook_page'

        ].empty_label = 'Select Facebook Page'



        # ========================================================

        # X

        # ========================================================



        self.fields[

            'selected_x_account'

        ].queryset = XCredential.objects.filter(

            is_active=True

        )



        self.fields[

            'selected_x_account'

        ].required = False



        self.fields[

            'selected_x_account'

        ].empty_label = 'Select X Account'



        # ========================================================

        # AUTO PLATFORMS

        # ========================================================



        self.fields[

            'auto_platforms'

        ].choices = self.AUTO_PLATFORM_CHOICES



        # ========================================================

        # INITIAL VALUES

        # ========================================================



        if self.instance and self.instance.pk:



            workflow_mode = self.instance.workflow_mode or 'auto'



            self.initial.setdefault(

                'workflow_mode',

                workflow_mode

            )



            selected_platforms = []



            if self.instance.selected_youtube_channel:

                selected_platforms.append('youtube')



            if self.instance.selected_instagram_account:

                selected_platforms.append('instagram')



            if self.instance.selected_facebook_page:

                selected_platforms.append('facebook')



            if self.instance.selected_x_account:

                selected_platforms.append('x')



            self.initial.setdefault(

                'auto_platforms',

                selected_platforms

            )



        else:

            self.initial.setdefault(

                'workflow_mode',

                'auto'

            )



    # ============================================================
    # VIDEO CONTENT VALIDATION
    # ============================================================

    def clean_video_file(self):
        video_file = self.cleaned_data.get("video_file")

        # Video upload is optional because a YouTube URL can be used
        # as the source instead.
        if not video_file:
            return video_file

        if video_file.size <= 0:
            raise forms.ValidationError(
                "Uploaded video file is empty."
            )

        file_name = video_file.name or ""
        extension = ""
        if "." in file_name:
            extension = "." + file_name.rsplit(".", 1)[1].lower()

        if extension != ".mp4":
            raise forms.ValidationError(
                "Only MP4 video files are allowed."
            )

        # Extension alone is not trusted. Check the ISO Base Media
        # File Format signature used by MP4 containers.
        try:
            video_file.seek(0)
            header = video_file.read(32)
        finally:
            video_file.seek(0)

        if len(header) < 12 or header[4:8] != b"ftyp":
            raise forms.ValidationError(
                "The uploaded file is not a valid MP4 video."
            )

        major_brand = header[8:12].lower()
        allowed_brands = {
            b"isom",
            b"iso2",
            b"iso3",
            b"iso4",
            b"iso5",
            b"iso6",
            b"iso8",
            b"mp41",
            b"mp42",
            b"avc1",
            b"dash",
        }

        if major_brand not in allowed_brands:
            raise forms.ValidationError(
                "The uploaded file does not appear to be a supported MP4 video."
            )

        return video_file

    # ============================================================
    # VALIDATION
    # ============================================================

    def clean(self):

        cleaned_data = super().clean()



        workflow_mode = cleaned_data.get('workflow_mode')

        auto_platforms = cleaned_data.get('auto_platforms') or []



        youtube_channel = cleaned_data.get(

            'selected_youtube_channel'

        )



        instagram_account = cleaned_data.get(

            'selected_instagram_account'

        )



        facebook_page = cleaned_data.get(

            'selected_facebook_page'

        )



        x_account = cleaned_data.get(

            'selected_x_account'

        )



        # ========================================================

        # MANUAL SYSTEM

        # ========================================================



        if workflow_mode == 'manual':



            # Manual system does not require any platform/account

            # selection at upload time.

            return cleaned_data



        # ========================================================

        # AUTO SYSTEM

        # ========================================================



        if workflow_mode == 'auto':



            # At least one platform should be selected.

            if not auto_platforms:

                raise forms.ValidationError(

                    'Auto System ke liye kam se kam ek platform '

                    'select karein.'

                )



            # ----------------------------------------------------

            # YouTube

            # ----------------------------------------------------



            if 'youtube' in auto_platforms:

                if not youtube_channel:

                    self.add_error(

                        'selected_youtube_channel',

                        'YouTube select kiya hai, isliye '

                        'YouTube Channel select karein.'

                    )



            # ----------------------------------------------------

            # Instagram

            # ----------------------------------------------------



            if 'instagram' in auto_platforms:

                if not instagram_account:

                    self.add_error(

                        'selected_instagram_account',

                        'Instagram select kiya hai, isliye '

                        'Instagram Account select karein.'

                    )



            # ----------------------------------------------------

            # Facebook

            # ----------------------------------------------------



            if 'facebook' in auto_platforms:

                if not facebook_page:

                    self.add_error(

                        'selected_facebook_page',

                        'Facebook select kiya hai, isliye '

                        'Facebook Page select karein.'

                    )



            # ----------------------------------------------------

            # X

            # ----------------------------------------------------



            if 'x' in auto_platforms:

                if not x_account:

                    self.add_error(

                        'selected_x_account',

                        'X select kiya hai, isliye X Account select karein.'

                    )



        return cleaned_data