from celery import shared_task

from .views import background_process_video


@shared_task
def process_video_task(video_id):
    background_process_video(video_id)
