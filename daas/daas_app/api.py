from rest_framework.response import Response
from rest_framework.views import APIView
import hashlib
from rest_framework.parsers import MultiPartParser
from rest_framework.parsers import JSONParser
from rest_framework.parsers import MultiPartParser, FormParser, FileUploadParser

from .models import Sample
from .utils.reprocess import reprocess
from .serializers import SampleWithoutDataSerializer, SampleSerializer, ResultSerializer
from .utils.upload_file import upload_file
from .utils.callback_manager import CallbackManager


class AbstractSampleAPIView(APIView):
    def serialized_response(self, samples, request):
        """
        :param samples: Sample queryset
        :return: Response that should return every subclass of this one
        """
        serializer = SampleSerializer(samples, many=True, context={'request': request})
        return Response(serializer.data)


class GetSamplesFromHashAPIView(AbstractSampleAPIView):
    parser_classes = (JSONParser,)

    def post(self, request):
        md5s = request.data.get('md5', [])
        sha1s = request.data.get('sha1', [])
        sha2s = request.data.get('sha2', [])
        return self.serialized_response(Sample.objects.with_hash_in(md5s, sha1s, sha2s), request)


class GetSamplesFromFileTypeAPIView(AbstractSampleAPIView):
    def get(self, request):
        file_types = request.query_params.get('file_type').split(',')
        return self.serialized_response(Sample.objects.with_file_type_in(file_types), request)


class GetSamplesWithSizeBetweenAPIView(AbstractSampleAPIView):
    def get(self, request):
        lower_size = request.query_params.get('lower_size')
        top_size = request.query_params.get('top_size')
        return self.serialized_response(Sample.objects.with_size_between(lower_size, top_size), request)


class UploadAPIView(APIView):
    parser_classes = (MultiPartParser,)

    def post(self, request):
        name = request.data['name']
        content = request.data['file'].read()
        force_reprocess = request.data.get('force_reprocess', False)
        callback = request.data.get('callback', None)
        is_new = upload_file(name, content, force_reprocess)
        if callback is not None:
            if is_new:
                CallbackManager().add_url(callback, hashlib.sha1(content).hexdigest())
            else:
                CallbackManager().call(callback, hashlib.sha1(content).hexdigest())
        return Response(status=202)


class ReprocessAPIView(APIView):
    def post(self, request):
        md5s = request.data.get('md5', [])
        sha1s = request.data.get('sha1', [])
        sha2s = request.data.get('sha2', [])
        force_reprocess = request.data.get('force_reprocess', False)

        samples = Sample.objects.with_hash_in(md5s, sha1s, sha2s)
        if not force_reprocess:
            # Return data for samples processed with the latest decompiler.
            for sample in samples.processed_with_current_decompiler_version():
                CallbackManager().call(request.POST.get('callback'), sample.sha1)
            samples = samples.processed_with_old_decompiler_version()

        # Reprocess and add a callback for samples processed with old decompilers.
        for sample in samples:
            CallbackManager().add_url(request.POST.get('callback'), sample.sha1)
            reprocess(sample, force_reprocess=force_reprocess)

        return Response(status=202)
