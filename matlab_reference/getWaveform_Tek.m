function [YUNits,YUNits_raw] = getWaveform_Tek(File,deviceNum,scopeChannel)
%% Variables
% Scope
File = getSettings_Tek(File,deviceNum,scopeChannel);
isChannelOn = File.Oscilloscope(deviceNum).Settings.Status;
if isChannelOn
    startTime = tic;
    % make = File.Oscilloscope(deviceNum).Make;
    model = File.Oscilloscope(deviceNum).Model;
    isTPS = contains2(model,'tps');
    oscilloscope = File.Oscilloscope(deviceNum).Object;
    dataWidth = File.Oscilloscope(deviceNum).Settings.DataWidth;
    switch dataWidth
        case 1
            bits_use = 'int8';
        case 2
            bits_use = 'int16';
    end

    dataEncoding = File.Oscilloscope(deviceNum).Settings.DataEncoding;
    if ~contains2(dataEncoding,{'ASC','BIN'}) || ~ischar(dataEncoding)
        fprintf(oscilloscope,'WFMPre:ENCdg?');
        dataEncoding = fgetl2(oscilloscope);
        File.Oscilloscope(deviceNum).Settings.DataEncoding = dataEncoding;
    end
    isBinary = contains2(dataEncoding,'BIN');

    %% Function
    channelSelect_cell = File.Oscilloscope(deviceNum).Channels;
    channelNames_cell = File.Oscilloscope(deviceNum).ChannelNames;
    channelScalings_arr = File.Oscilloscope(deviceNum).Scalings;
    channel_idx = find(contains(channelSelect_cell,scopeChannel,'IgnoreCase',true));
    channel = channelSelect_cell{channel_idx};
    channelName = channelNames_cell{channel_idx};
    monitorScale = channelScalings_arr(channel_idx);
    fprintf('\tCapturing %s %s %s...',model,channel,channelName);
    fprintf(oscilloscope,'ACQuire:STAte RUN');
    % fprintf(oscilloscope,'ACQuire:STOPAfter SEQuence');

    % Curve data
    channelStatus_use = sprintf('SELect:%s?',scopeChannel);
    if isTPS
        attempts = 2;
    else
        attempts = 1;
    end
    for attemptNum = 1:attempts
        fprintf(oscilloscope,channelStatus_use);
        %     channelStatus = fgetl2(oscilloscope);
        if isTPS
            pause(0.5);
        end
    end

    fprintf(oscilloscope,'CURVe?');        % query waveform data
    if isBinary
        CURVe = binblockread(oscilloscope,bits_use);
    else
        CURVe_raw = fgetl2(oscilloscope);
        CURVe = str2double(CURVe_raw); % % get numeric waveform data
    end
    % readTime = toc(startTime);
    % fprintf('(%.2f s)...',readTime);

    % Get constants to convert digital to analog
    fprintf('processing...');

    % Conversion factor
    YZEro = File.Oscilloscope(deviceNum).Settings.WaveformConversion;
    while isempty(YZEro) || isnan(YZEro) || YZEro == 1
        fprintf(oscilloscope,'WFMPre:YZEro?');	% query conversion factor
        YZEro_raw = fgetl2(oscilloscope);
        YZEro = str2double(YZEro_raw); % get conversion factor number
        File.Oscilloscope(deviceNum).Settings.WaveformConversion = YZEro;
    end

    % Vertical scale factor
    YMUlt = File.Oscilloscope(deviceNum).Settings.VerticalScaleFactor;
    while isempty(YMUlt) || isnan(YMUlt) || ~any(YMUlt) || abs(YMUlt) > 1
        fprintf(oscilloscope,'WFMPre:YMUlt?');	% query vertical scale factor
        YMUlt_raw = fgetl2(oscilloscope);
        YMUlt = str2double(YMUlt_raw);% get vertical scale factor number
        File.Oscilloscope(deviceNum).Settings.VerticalScaleFactor = YMUlt;
    end

    % Vertical position
    YOFf = File.Oscilloscope(deviceNum).Settings.VerticalPosition;
    while isempty(YOFf) || isnan(YOFf) || ~(rem(YOFf,1) == 0 || ~any(YOFf))
        fprintf(oscilloscope,'WFMPre:YOFf?');	% query vertical position
        YOFf_raw = fgetl2(oscilloscope);
        YOFf = str2double(YOFf_raw);  % get vertical position number
        File.Oscilloscope(deviceNum).Settings.VerticalPosition = YOFf;
    end

    % Convert digitized waveform
    YUNits_monitor = YZEro + YMUlt * (CURVe - YOFf);    % vertical units
    YUNits_scaled = YUNits_monitor / monitorScale;  % actual unit
    YUNits_raw = transpose(YUNits_scaled);
    YUNits = smooth(YUNits_raw);                 % smoothed
    fprintf(oscilloscope,'ACQuire:STAte RUN');
else
    YUNits_raw = [];
    YUNits = [];
end

if isChannelOn
    [endTime,unit] = getEndTime(startTime);
    fprintf('OK (%.2f %s)\n',endTime,unit);
end

% fprintf(oscilloscope,'ACQuire:STAte ON');

end