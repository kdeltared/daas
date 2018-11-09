from django.db import models
from .utils import redis_status
from .utils.redis_manager import RedisManager
from .config import ALLOW_SAMPLE_DOWNLOAD
import logging
from .decompilers.decompiler_config import configs, get_identifiers
from datetime import datetime
from django.db.models import Count, DateField
from django.db.models.functions import Trunc


class SampleManager(models.Manager):
    def with_size_between(self, size_from, size_to):
        """ Returns all samples which size is in between [size_from, size_to) """
        return self.filter(size__gte=size_from, size__lt=size_to)

    def with_elapsed_time_between(self, elapsed_time_from_, elapsed_time_to):
        return self.filter(statistics__elapsed_time__gte=elapsed_time_from_,
                           statistics__elapsed_time__lte=elapsed_time_to)

    def count_by_file_type(self, queryset=None):
        """ Returns {'c#': 22, 'flash': 3, ....} """
        if queryset is None:
            queryset = self.all()
        result = {}
        for file_type in get_identifiers():
            result.update({file_type: queryset.filter(file_type=file_type).count()})
        return result

    def failed(self):
        return self.filter(statistics__decompiled=False).filter(statistics__timed_out=False)

    def decompiled(self):
        return self.filter(statistics__decompiled=True)

    def timed_out(self):
        return self.filter(statistics__timed_out=True)

    def samples_per_upload_date(self):
        return self.__count_per_date('datetime')

    def samples_per_processed_date(self):
        return self.__count_per_date('statistics__datetime')

    def __count_per_date(self, date_):
        # We need an order_by here because Sample class has a default order_by. See:
        # https://docs.djangoproject.com/en/2.1/topics/db/aggregation/#interaction-with-default-ordering-or-order-by
        counts = self.annotate(date=Trunc(date_, 'day', output_field=DateField())).values('date').annotate(count=Count('*')).order_by()
        count_dict = {}
        for element in counts:
            if element['date'] is not None:
                count_dict[element['date']] = element['count']
        return count_dict

    def first_date(self):
        return self.last().datetime.date()


class Sample(models.Model):
    class Meta:
        ordering = ['-id']
    # MD5 is weak, so it's better to not use unique=True here.
    md5 = models.CharField(max_length=100, db_index=True)
    sha1 = models.CharField(max_length=100, unique=True)
    sha2 = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=200)
    # We do not need unique here because sha1 constraint will raise an exception instead.
    data = models.BinaryField(default=0, blank=True, null=True)
    size = models.IntegerField()
    datetime = models.DateTimeField(auto_now=True)
    file_type = models.CharField(max_length=50, blank=True, null=True, db_index=True)

    objects = SampleManager()

    def __str__(self):
        return self.name

    def all_redis_jobs(self):
        return RedisJob.objects.filter(sample=self)

    def redis_job(self):
        return self.all_redis_jobs().latest('datetime')

    def status(self):
        self.redis_job().update()
        return self.redis_job().status

    def finished(self):
        self.redis_job().update()
        return self.redis_job().finished()

    def unfinished(self):
        return not self.finished()

    def cancel_job(self):
        self.redis_job().cancel()

    def delete(self, *args, **kwargs):
        if self.all_redis_jobs().count() > 0:
            self.cancel_job()
        super().delete(*args, **kwargs)

    def decompiled(self):
        try:
            return self.statistics.decompiled
        except AttributeError:
            return False

    def content_saved(self):
        return self.data is not None

    def downloadable(self):
        return self.content_saved() and ALLOW_SAMPLE_DOWNLOAD


class Statistics(models.Model):
    timeout = models.IntegerField(default=None, blank=True, null=True, db_index=True)
    elapsed_time = models.IntegerField(default=None, blank=True, null=True, db_index=True)
    exit_status = models.IntegerField(default=None, blank=True, null=True, db_index=True)
    # In most cases (99%+) it will be False, so it makes sense to create an index of a boolean column
    timed_out = models.BooleanField(default=False, db_index=True)
    output = models.CharField(max_length=65000)
    zip_result = models.BinaryField(default=None, blank=True, null=True)
    # In most cases (99%+) it will be True, so it makes sense to create an index of a boolean column
    decompiled = models.BooleanField(default=False, db_index=True)
    decompiler = models.CharField(max_length=100, db_index=True)
    sample = models.OneToOneField(Sample, on_delete=models.CASCADE)
    datetime = models.DateTimeField(auto_now=True)
    version = models.IntegerField(default=0, db_index=True)

    def file_type(self):
        return self.sample.file_type

    def get_config(self):
        for config in configs:
            if config['identifier'] == self.file_type():
                return config
        raise Exception('Missing configuration with identifier: %s' % self.file_type())

    def decompiled_with_latest_version(self):
        return self.version == self.get_config().get('version', 0)

    def failed(self):
        return (not self.decompiled) and (not self.timed_out)


class RedisJob(models.Model):
    job_id = models.CharField(db_index=True, max_length=100)
    status = models.CharField(default=redis_status.QUEUED, max_length=len(redis_status.PROCESSING))
    datetime = models.DateTimeField(auto_now=True)
    sample = models.ForeignKey(Sample, on_delete=models.CASCADE)

    def __set_status(self, status):
        # If we don't use 'save' method here, race conditions will happen and lead to incorrect status.
        logging.debug('Redis job %s changing status: %s -> %s' % (self.job_id, self.status, status))
        self.status = status
        self.save()

    def update(self):
        if not self.finished():
            job = RedisManager().get_job(self.sample.file_type, self.job_id)
            if job is None:
                self.__set_status(redis_status.DONE if self.sample.decompiled() else redis_status.FAILED)
            elif job.is_finished:
                self.__set_status(redis_status.DONE)
            elif job.is_queued:
                self.__set_status(redis_status.QUEUED)
            elif job.is_started:
                self.__set_status(redis_status.PROCESSING)
            elif job.is_failed:
                self.__set_status(redis_status.FAILED)

    def finished(self):
        return self.status in [redis_status.DONE, redis_status.FAILED, redis_status.CANCELLED]

    def is_cancellable(self):
        return self.status == redis_status.QUEUED

    def is_cancelled(self):
        return self.status == redis_status.CANCELLED

    def cancel(self):
        if self.is_cancellable():
            RedisManager().cancel_job(self.sample.file_type, self.job_id)
            self.__set_status(redis_status.CANCELLED)
