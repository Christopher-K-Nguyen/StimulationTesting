function varargout = getWaveform2(File,varargin)
%% Constants
SCALE_MIN = 2e-3;
CURRENT_RANGE_MIN = 40;
ASYMM_CHECK = 3;
MAX_FACTOR = 3.9;

%% Variables
numOfVar = length(varargin);
if numOfVar > 1
    deviceNum = varargin{1};
    scopeChannel = varargin{2};
    if numOfVar > 2
        type = varargin{3};
        if ~isempty(type)
            isGetFull = logical(type);
        else
            isGetFull = true;
        end
    else
        isGetFull = true;
    end
else
    deviceNum = 1;
    scopeChannel = varargin{1};
    isGetFull = true;
end
isMathChannel = contains2(scopeChannel,'MATH');
rescale = 200e-3;

if isGetFull
    % Channel
    channel_arr = [File.Data(:).ActiveChannel];
    groupNum = length(channel_arr);
    % channelNum = channel_arr(groupNum);

    % Capture
    capture_arr = [File.Data(groupNum).Capture.Index];
    numOfCaptures = length(capture_arr);
    captureNum = capture_arr(numOfCaptures);

    % Pattern
    amplitude1 = File.Data(groupNum).Capture(captureNum).Amplitude;

    % Time
    time = File.Data(groupNum).Capture(captureNum).Time;

    % Fine Scale
    scale_min_use = SCALE_MIN;
else
    digitalDelay = File.Stimulator.DigitalDelay;
    [File,time_raw] = getTime(File);
    time = time_raw * 1e6 - digitalDelay;
    amplitude1 = 1;
    scale_min_use = SCALE_MIN * 2.5;
end
isSymmetric = File.Parameters.Symmetry;
hasAmplitude = any(amplitude1);
pulseWidth_arr = File.Parameters.PulseWidth;
pulseWidth = max(pulseWidth_arr);
configID = File.Parameters.Configuration.ID;
isMP = contains2(configID,'MP');
isPBP = contains2(configID,'PBP');
isBP = contains2(configID,'BP') && ~isPBP;
isPTP = contains2(configID,'PTP');
isTP = contains2(configID,'TP') && ~isPTP;
isPartial = isPBP || isPTP;
isCG = contains2(configID,'CG');

% Vertical scale
vertScale_diff1 = 2;
vertScale_part1 = (2:vertScale_diff1:10-vertScale_diff1) * 1e-3;
vertScale_diff2 = 10;
vertScale_part2 = (10:vertScale_diff2:100-vertScale_diff2) * 1e-3;
vertScale_diff3 = 50;
vertScale_part3 = (100:vertScale_diff3:1e3-vertScale_diff3) * 1e-3;
vertScale_diff4 = 100;
vertScale_part4 = (1e3:vertScale_diff4:2e3-vertScale_diff4) * 1e-3;
vertScale_diff5 = 200;
vertScale_part5 = (2e3:vertScale_diff5:5e3) * 1e-3;
vertScale_arr = [ ....
    vertScale_part1 ...
    vertScale_part2 ...
    vertScale_part3 ...
    vertScale_part4 ...
    vertScale_part5];
numOfScaled = length(vertScale_arr);
vertScale_min = min(vertScale_arr);
vertRange = vertScale_arr * 8;
vertRangeMin = SCALE_MIN * 40;

% Channels
channelSelect_cell = File.Oscilloscope(deviceNum).Channels;
hasMathChannel = contains2(channelSelect_cell,'MATH');
channelName_cell = File.Oscilloscope(deviceNum).ChannelNames;
scopeChannel_tf = containsi(channelSelect_cell,scopeChannel);
channelName = channelName_cell{scopeChannel_tf};
isCurrentChannel = contains2(channelName,'curr');
isVoltChannel = ~isCurrentChannel;
if contains2(channelName,'volt')
    monitorScale = File.Stimulator.VoltageScaling;
elseif isCurrentChannel
    monitorScale = File.Stimulator.CurrentScaling;
else
    monitorScale = 1;
end
isVoltageChannel = contains2(channelName,'volt');
isReturnChannel = contains2(channelName,{'ret','count'});
isPotential = contains2(channelName,{'act','work','pot','diff','ret','count'});
isReturnSmall = isReturnChannel && (isMP || isTP || isPartial || isCG);
if isMathChannel
    position_query = sprintf('%s:VERtical:POSition?',scopeChannel);
    scale_query = sprintf('%s:VERtical:SCAle?',scopeChannel);
else
    position_query = sprintf('%s:POSition?',scopeChannel);
    scale_query = sprintf('%s:SCAle?',scopeChannel);
end
isCurrentSymmetric = isCurrentChannel && isSymmetric;
isCurrentAsymmetric = isCurrentChannel && ~isSymmetric;
% isViewCheckNeeded = ~isSymmetric || (isVoltChannel && (isMP || isTP || isPartial || isCG));


% Oscilloscope
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
    updateWaitbar(File,'Getting waveform encoding...');
    fprintf(oscilloscope,'WFMPre:ENCdg?');
    dataEncoding = fgetl2(oscilloscope);
    File.Oscilloscope(deviceNum).Settings.DataEncoding = dataEncoding;
end
isBinary = contains2(dataEncoding,'BIN');
fprintf('%s %s %s...\n',model,scopeChannel,channelName);

% Settings
updateWaitbar(File,['Getting ' channelName ' settings...']);
fprintf('\t\t');
File = getSettings(File,deviceNum,scopeChannel);

% Initialization
try
    offset = File.Stimulator.Offset(groupNum).(scopeChannel);
catch
    offset = [];
end
dataLength = File.Oscilloscope(deviceNum).Settings.DataLength;
vertScale_count = zeros(numOfScaled,1);
vertScale_check = [];
fineScale_arr = [];
isInRange = false;
isScaleRepeated = false;
isScaleUsed = false;
isInView_check = false;
isAtScaleMin = false;
isGetFine = false;
isFineScale = false;
isCoarseScale = [];
fineScale_count = 0;

% Capture Waveform
if captureNum > 1 && ~isReturnSmall
    pos_fix = 0;
    pos_new = str2double(sprintf('%.2e',pos_fix));
    pos_use = sprintf('%s:POSition %.2e',scopeChannel,pos_new);
    if isTPS
        fprintf(oscilloscope,'*CLS');
        startTime = tic;
        pos_check = [];
        pos_query = sprintf('%s:POSition?',scopeChannel);
        while ~isequal(pos_check,pos_new)
            fprintf(oscilloscope,pos_use);
            fprintf(oscilloscope,pos_query);
            pos_raw = fgetl2(oscilloscope);
            pos_check = str2double(pos_raw);
        end
    else
        fprintf(oscilloscope,pos_use);
    end
end

count = 0;
while ~isInRange
    % fprintf('\t%s %s %s...',model,scopeChannel,channelName);
    startTime = tic;
    count = count + 1;
    fprintf(oscilloscope,'ACQuire:STAte RUN');
    updateWaitbar(File);
    % Curve data
    fprintf('\tCapturing...');
    fprintf(oscilloscope,'ACQuire:STAte RUN');
    updateWaitbar(File,'Capturing....');
    % if isReturnSmall
    % fprintf(oscilloscope,'ACQuire:STOPAfter SEQuence'); % RUNSTop
    % fprintf(oscilloscope,'*WAI');
    numOfAcq = 36;
    numOfAcq_check = 0;
    acquTime = tic;
    while numOfAcq_check <= numOfAcq
        fprintf(oscilloscope,'ACQuire:NUMACq?');
        numOfAcq_raw = fgetl2(oscilloscope);
        numOfAcq_check = str2double(numOfAcq_raw);
        % fprintf('%s ',numOfAcq_raw);
        updateWaitbar(File);
        if toc(acquTime) > 2
            break;
        end
    end
    % end
    fprintf(oscilloscope,'CURVe?');        % query waveform data
    if isBinary
        CURVe = binblockread(oscilloscope,bits_use);
    else
        CURVe_raw = fgetl2(oscilloscope);
        CURVe = str2double(CURVe_raw); % % get numeric waveform data
    end
    updateWaitbar(File,'Processing');
    % if isReturnSmall
    % fprintf(oscilloscope,'ACQuire:STAte ON');
    % fprintf(oscilloscope,'ACQuire:STAte RUN');
    % end

    % Get constants to convert digital to analog
    fprintf('processing...');

    % Conversion factor
    % fprintf('(conversion...');
    YZEro = File.Oscilloscope(deviceNum).Settings.WaveformConversion;

    % Vertical scale factor
    % fprintf('scale...');
    YMUlt = File.Oscilloscope(deviceNum).Settings.VerticalScaleFactor;

    % Vertical position
    % fprintf('position)...');
    YOFf = File.Oscilloscope(deviceNum).Settings.VerticalPosition;

    % Convert digitized waveform
    YUNits_monitor = YZEro + YMUlt * (CURVe - YOFf);    % vertical units
    YUNits_brefore = mean(YUNits_monitor(time < 0));
    if (isVoltageChannel || isCurrentChannel)
        YUNits_scaled = (YUNits_monitor - YUNits_brefore) / monitorScale;  % actual unit
        if isnan(YUNits_scaled)
            % fprintf('YUNits_scaled\n');
        end
    else
        YUNits_scaled = YUNits_monitor / monitorScale;  % actual unit
    end

    YUNits_raw = transpose(YUNits_scaled);
    if ~isempty(offset)
        if isVoltageChannel
            YUNits_fix = YUNits_raw - offset;
        elseif isVoltChannel
            offset_prepulse = mean(offset(time < 0));
            YUNits_fix = (YUNits_raw - offset_prepulse) - (offset - offset_prepulse) + offset_prepulse;
        else
            YUNits_fix = YUNits_raw;
        end
    else
        YUNits_fix = YUNits_raw;
    end

    YUNits = smooth(YUNits_fix,7);

    if isReturnChannel && isGetFull
        YUNits_prepulse = mean(YUNits(time < 0));
        setArduinoVoltage(File,YUNits_prepulse);
    end
    [~,~,r2] = getLinReg(YUNits_monitor);
    if fineScale_count > 1
    else
        YUNits_min = min(YUNits_monitor);  % min
        YUNits_max = max(YUNits_monitor);  % max
    end

    % Check view
    position_check = [];
    if isTPS
        fprintf(oscilloscope,'*CLS');
    end
    while isempty(position_check)
        updateWaitbar(File,'Getting position...');
        fprintf(oscilloscope,position_query);
        position_raw = fgetl2(oscilloscope);
        position_check = str2double(position_raw);
    end

    scale_check = [];
    if isTPS
        fprintf(oscilloscope,'*CLS');
    end
    while isempty(scale_check)
        updateWaitbar(File,'Getting scale...');
        fprintf(oscilloscope,scale_query);
        scale_raw = fgetl2(oscilloscope);
        scale_check = str2double(scale_raw);
    end
    % position = -position_check * scale_check;
    % range_min = MAX_FACTOR * -scale_check + position;
    % range_max = MAX_FACTOR * scale_check + position;
    YUNits_range = max(YUNits_monitor) - min(YUNits_monitor);
    %         range_check = range_max - range_min;

    if fineScale_count > 2
        isInRange = true;
    end

    varargout{1} = YUNits;
    varargout{2} = YUNits_raw;
    % fprintf(oscilloscope,'ACQuire:STAte RUN');

    % Adjust scaling
    if isAtScaleMin || ((isScaleRepeated || isScaleUsed) && isInView_check)
        isInRange = true;
    end
    if ~isInRange && hasAmplitude
        % if isTPS
            fprintf(oscilloscope,'*CLS');
        % end

        % Scale
        vertScale = [];
        while isempty(vertScale) || isnan(vertScale)
            fprintf(oscilloscope,scale_query);
            vertScale_raw = fgetl2(oscilloscope);
            vertScale = str2double(vertScale_raw);
        end

        % Position
        pos_factor = [];
        while isempty(pos_factor) || isnan(pos_factor)
            updateWaitbar(File,'Getting position...');
            fprintf(oscilloscope,position_query);
            pos_raw = fgetl2(oscilloscope);
            pos_factor = str2double(pos_raw);
        end


        % if fineScale_count > 1
        % else
            vertPos = -pos_factor * vertScale;
            range_min_check = MAX_FACTOR * -vertScale + vertPos;
            range_max_check = MAX_FACTOR * vertScale + vertPos;
        % end
        inView_tf = isbetween2(...
            [min(YUNits_monitor) max(YUNits_monitor)],...
            range_min_check,range_max_check, ...
            'open');
        inView_idx = find(inView_tf);
        inView_len = length(inView_idx);
        inView_fract = inView_len / dataLength;
        isInView_check = all(inView_tf) ;%|| inView_fract > 0.99;

        % Fine scaling
        isGetFine = ~isSymmetric || r2 > 0.80 || (~isBP && isReturnSmall);
        %|| (~isMathChannel && (YUNits_range < vertRangeMin || isReturnSmall));
        if isGetFine
            isCoarseScale = false;
            % fractDiff = 0.6;
            % isTooSmall = ...
            %     ((range_min_check - YUNits_min) / range_min_check < fractDiff) || ...
            %     ((range_max_check - YUNits_max) / range_max_check > fractDiff);
            
            if isFineScale && isInView_check
                fprintf('fine fit...');
                isInRange = true;
            end

            if fineScale_count > 1
                fineScale_arr_alloc = [fineScale_arr;scale_new];
                fineScale_arr = fineScale_arr_alloc;
            end
        end

        if isCurrentChannel
            isInRange = true;
        end
        if ~isInRange
            if ~isGetFine && (logical2(isCoarseScale) || isempty(isCoarseScale))
                % Adjust coarse scaling
                isCoarseScale = true;

                % Coarse scaling: maximize waveform visibility
                % vertScale_idx = find(YUNits_range > vertRange,1);
                vertScale_idx = find(vertScale_arr > (YUNits_range / 8 * 1.0),1);
                if ~isInView_check
                    vertScale_idx = vertScale_idx + 5;
                end
                if vertScale_idx < 1 ...
                        || any(isnan(vertScale_idx)) ...
                        || isempty(vertScale_idx)
                    vertScale_idx = 1;
                end
                scale = vertScale_arr(vertScale_idx);
                vertScale_check_alloc = [vertScale_check;scale];
                vertScale_check = vertScale_check_alloc;
                vertScale_count(vertScale_idx) = vertScale_count(vertScale_idx) + 1;

                % Check repeats
                isScaleRepeated = any(vertScale_count > 1);
                isScaleUsed = sum(ismember(vertScale_check,scale)) > 1;
                if (isScaleUsed || isScaleRepeated) %&& isInView_check
                    fprintf('coarse repeated...');
                    isInRange = true;
                elseif scale == vertScale_min
                    fprintf('min coarse...');
                    isInRange = true;
                else
                    %                     if count > 1
                    %                         fprintf('Coarse ');
                    %                     else
                    fprintf('coarse ');
                    %                     end
                    fprintf('scaling ');
                    if isMathChannel
                        scale_use = sprintf('%s:VERtical:SCAle %.2e',scopeChannel,scale);
                    else
                        scale_use = sprintf('%s:SCAle %.2e',scopeChannel,scale);
                    end
                    %                     len = length(vertScale_check);
                    while any(isnan(scale) | isempty(scale))
                        len = len - 1;
                        fprintf('\n');
                        display(vertScale_idx);
                        display(scale);
                        %                         scale = vertScale_check(len);
                    end
                    if isTPS
                        scale_check = [];
                        while ~isequal(scale_check,scale)
                            updateWaitbar(File,'Getting scale...');
                            fprintf(oscilloscope,scale_use);
                            fprintf(oscilloscope,scale_query);
                            scale_raw = fgetl2(oscilloscope);
                            scale_check = str2double(scale_raw);
                        end
                    else
                        updateWaitbar(File,'Getting scale...');
                        fprintf(oscilloscope,scale_use);
                    end
                    fprintf('(%.2e V)...',scale);
                end
            else
                if (isGetFine || isFineScale) && ~(logical2(isCoarseScale) || isempty(isCoarseScale))
                    % Adjust fine scaling
                    updateWaitbar(File,'Fine scaling and position...');
                    fprintf('fine ');
                    [scale_new,~] = setFineScalePos2(File, ...
                        scopeChannel,YUNits_min,YUNits_max,fineScale_count);
                    isFineScale = true;
                    fineScale_count = fineScale_count + 1;
                end
            end
        else
            % isInRange = true;
        end
        fprintf(oscilloscope,'ACQuire:STAte RUN');
        if ~isInRange
            [endTime,unit] = getEndTime(startTime);
            fprintf('OK \t\t(%.2f %s)\n',endTime,unit);
            fprintf('\t\t');
            File = getSettings(File,deviceNum,scopeChannel);
        else
            [endTime,unit] = getEndTime(startTime);
            fprintf('OK \t\t(%.2f %s)\n',endTime,unit);
        end
    else
        [endTime,unit] = getEndTime(startTime);
        fprintf('OK \t\t(%.2f %s)\n',endTime,unit);
        isInRange = true;
        % break;
    end
    fprintf(oscilloscope,'ACQuire:STAte RUN');
end
updateWaitbar(File,[channelName ' captured.']);

end