# Generated by Django 2.0.6 on 2018-09-24 20:07

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('daas_app', '0004_auto_20180924_2003'),
    ]

    operations = [
        migrations.AlterField(
            model_name='sample',
            name='command_output',
            field=models.CharField(blank=True, default='', max_length=65000, null=True),
        ),
        migrations.AlterField(
            model_name='sample',
            name='statistics',
            field=models.ForeignKey(blank=True, default=None, null=True, on_delete=django.db.models.deletion.CASCADE, to='daas_app.Statistics'),
        ),
        migrations.AlterField(
            model_name='sample',
            name='zip_result',
            field=models.BinaryField(blank=True, default=None, null=True),
        ),
    ]