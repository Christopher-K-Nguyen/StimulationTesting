function varargout = getWaveform(File,varargin)
%% Constants
SCALE_MIN = 2e-3;
CURRENT_RANGE_MIN = 40;
ASYMM_CHECK = 3;
MAX_FACTOR = 4;

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
vertScale_diff1 = 1;
vertScale_part1 = (2:vertScale_diff1:10-vertScale_diff1) * 1e-3;
vertScale_diff2 = 2;
vertScale_part2 = (10:vertScale_diff2:50-vertScale_diff2) * 1e-3;
vertScale_diff3 = 5;
vertScale_part3 = (50:vertScale_diff3:500-vertScale_diff3) * 1e-3;
vertScale_diff4 = 10;
vertScale_part4 = (500:vertScale_diff4:2e3-vertScale_diff4) * 1e-3;
vertScale_diff5 = 20;
vertScale_part5 = (2e3:vertScale_diff5:5e3) * 1e-3;
vertScale_arr = flip([ ....
    vertScale_part1 ...
    vertScale_part2 ...
    vertScale_part3 ...
    vertScale_part4 ...
    vertScale_part5]);
% vertScale_arr = flip([...
%     2e-3; ...    % 2 mV/div
%     5e-3; ...    % 5 mV/div
%     10e-3; ... 	% 10 mV/div
%     15e-3;...
%     20e-3; ...   % 20 mV/div
%     25e-3;...
%     30e-3;...
%     35e-3;...
%     40e-3; ...
%     45e-3;...
%     50e-3; ...   % 50 mV/div
%     55e-3;...
%     60e-3; ...
%     75e-3; ...
%     100e-3; ...  % 100 mV/div
%     125e-3; ...
%     150e-3; ...
%     175e-3; ...
%     200e-3; ...  % 200 mV/div
%     225e-3; ...
%     250e-3; ...
%     275e-3; ...
%     300e-3; ...
%     325e-3; ...
%     350e-3; ...
%     375e-3; ...
%     400e-3; ...
%     425e-3; ...
%     450e-3; ...
%     475e-3; ...
%     500e-3; ... 	% 500 mV/div
%     550e-3; ...
%     600e-3; ...
%     650e-3; ...
%     700e-3; ...
%     750e-3; ...
%     800e-3; ...
%     850e-3; ...
%     900e-3; ...
%     950e-3; ...
%     975e-3; ...
%     1.00; ...       % 1 V/div
%     1.25; ...
%     1.50; ...
%     1.75; ...
%     2.00; ...       % 2 V/div
%     2.25; ...
%     2.50; ...
%     2.75; ...
%     3.00; ...
%     3.25; ...
%     3.50; ...
%     3.75; ...
%     4.00; ...
%     4.25; ...
%     4.50; ...
%     4.75; ...
%     5.00]);     	% 5 V/div
numOfScaled = length(vertScale_arr);
vertScale_min = min(vertScale_arr);
vertRange = vertScale_arr * 4;
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

% Settings
updateWaitbar(File,['Getting ' channelName ' settings...']);
File = getSettings(File,deviceNum,scopeChannel);
% if (isMP || isPartial)
    fineScale_min = scale_min_use;
% elseif ~isSymmetric
%     fineScale_min = scale_min_use * 10;
% else
%     fineScale_min = scale_min_use * 2;
% end
fineScale_add = fineScale_min;

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
isAtScaleMin = false;
isGetFine = false;
isFineScale = false;
isFineScaleUsed = false;
isCoarseScale = [];
fineScale_count = 0;

% Capture
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

% Capture Waveform
count = 0;
while ~isInRange
    fprintf('\t%s %s %s...',model,scopeChannel,channelName);
    startTime = tic;
    count = count + 1;
    checkView_count = 0;
    reViewTime = tic;
    isInView = false;
    while ~isInView
        checkView_count = checkView_count + 1;
        fprintf(oscilloscope,'ACQuire:STAte RUN');
        updateWaitbar(File);
        % Curve data
        if checkView_count > 1
            fprintf('\tCapturing...');
        else
            fprintf('capturing...');
        end
        fprintf(oscilloscope,'ACQuire:STAte RUN');
        updateWaitbar(File,'Capturing....');
        % if isReturnSmall
        % fprintf(oscilloscope,'ACQuire:STOPAfter SEQuence'); % RUNSTop
        % fprintf(oscilloscope,'*WAI');
        numOfAcq = 48;
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
        % while isempty(YZEro) || isnan(YZEro) || YZEro == 1
        %     if isTPS
        %         fprintf(oscilloscope,'4CLS');
        %     end
        %     fprintf(oscilloscope,'WFMPre:YZEro?');	% query conversion factor
        %     YZEro_raw = fgetl2(oscilloscope);
        %     YZEro = str2double(YZEro_raw); % get conversion factor number
        %     File.Oscilloscope(deviceNum).Settings.WaveformConversion = YZEro;
        % end

        % Vertical scale factor
        % fprintf('scale...');
        YMUlt = File.Oscilloscope(deviceNum).Settings.VerticalScaleFactor;
        % while isempty(YMUlt) || isnan(YMUlt) || ~any(YMUlt) || abs(YMUlt) > 1
        %     if isTPS
        %         fprintf(oscilloscope,'*CLS');
        %     end
        %     fprintf(oscilloscope,'WFMPre:YMUlt?');	% query vertical scale factor
        %     YMUlt_raw = fgetl2(oscilloscope);
        %     YMUlt = str2double(YMUlt_raw);% get vertical scale factor number
        %     File.Oscilloscope(deviceNum).Settings.VerticalScaleFactor = YMUlt;
        % end

        % Vertical position
        % fprintf('position)...');
        YOFf = File.Oscilloscope(deviceNum).Settings.VerticalPosition;
        % while isempty(YOFf) || isnan(YOFf)
        %     if isTPS
        %         fprintf(oscilloscope,'*CLS');
        %     end
        %     fprintf(oscilloscope,'WFMPre:YOFf?');	% query vertical position
        %     YOFf_raw = fgetl2(oscilloscope);
        %     YOFf = str2double(YOFf_raw);  % get vertical position number
        %     File.Oscilloscope(deviceNum).Settings.VerticalPosition = YOFf;
        % end

        % Convert digitized waveform
        YUNits_monitor = YZEro + YMUlt * (CURVe - YOFf);    % vertical units
%         waveformRange = range(YUNits_monitor);
%         if waveformRange < SCALE_MIN
%             fineScale_min = SCALE_MIN / 2;
%         else
%             fineScale_min = waveformRange / 10;
%         end
%         fineScale_min = max(fineScale_min, SCALE_MIN);
%         fineScale_add = fineScale_min / 2;
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

        % if pulseWidth > 100
        % YUNits = smooth(YUNits_raw);
        % else
        %     if isVoltChannel && range(YUNits_raw) < 40e-3
        %         YUNits = smooth(YUNits_raw,35);
        %     else
        YUNits = smooth(YUNits_fix,7);
        %     end
        % end

        if isReturnChannel && isGetFull
            YUNits_prepulse = mean(YUNits(time < 0));
            setArduinoVoltage(File,YUNits_prepulse);
        end
        [~,~,r2] = getLinReg(YUNits_monitor);
        YUNits_min = min(YUNits_monitor);  % min
        YUNits_max = max(YUNits_monitor);  % max
        YUNits_mean = mean(YUNits_monitor);
        YUNits_range = range(YUNits_monitor);

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
        position = -position_check * scale_check;
        range_min = MAX_FACTOR * -scale_check + position;
        range_max = MAX_FACTOR * scale_check + position;
        YUNits_range = YUNits_max - YUNits_min;
%         range_check = range_max - range_min;
        isBetween_tf = isbetween2(YUNits_monitor,range_min,range_max);
        numInBetween = sum(isBetween_tf);
        checkLength = dataLength * 0.95;
        isInView = numInBetween >= checkLength;
        % if isCurrentSymmetric
        %     isInRange = true;
        % end

        range_min_check = MAX_FACTOR * -scale_check + position;
        range_max_check = MAX_FACTOR * scale_check + position;
        fractDiff = 0.2;
        isTooSmall = ...
            ((range_min_check - YUNits_min) / range_min_check > fractDiff) || ...
            ((range_max_check - YUNits_max) / range_max_check < -fractDiff);
        if isTooSmall
        end

        if checkView_count > 2 || isCurrentAsymmetric
            if isGetFine
                isInView = true;
            end
            % if isFineScale
%             if ~(fineScale_count > 1)
%                 rescale = rescale + 250e-3;
%             end
        end
        updateWaitbar(File,'Checking...');
                fprintf('checking...');
        if ~isInRange && ~isInView
            if logical2(isCoarseScale) || isempty(isCoarseScale)
                isInView = true;
            elseif (isFineScale || (~(isScaleUsed || isScaleRepeated)))
%             if ~isInView && ~isTooSmall % (~isInView_check || isValueOff)
%                 fprintf('out of view...');
%                 if ~isFineScale
                    reposition = 0;
%                 else
%                     if fineScale_count > 0
%                         if YUNits_max > range_max
%                             isInRange = false;
%                             reposition = (YUNits_max - range_max) / (MAX_FACTOR * scale_new);
%                         elseif YUNits_min < range_min
%                             reposition = (range_min - YUNits_min) / (MAX_FACTOR * scale_new);
%                             isInRange = false;
%                         else
%                             reposition = 0;
%                         end
%                     else
%                     end
%                     reposition = round(reposition,3,'significant');
%                 end
                if isMathChannel
                    reposition_use = sprintf('%s:VERtical:POSition %.2e',scopeChannel,reposition);
                    rescale_use = sprintf('%s:VERtical:SCAle %.2e',scopeChannel,rescale);
                else
                    reposition_use = sprintf('%s:POSition %.2e',scopeChannel,reposition);
                    rescale_use = sprintf('%s:SCAle %.2e',scopeChannel,rescale);
                end

                fprintf('repositioning ');
                if isTPS
                    fprintf(oscilloscope,'*CLS');
                    reposition_check = [];
                    while ~isequal(reposition_check,reposition_fix)
                        updateWaitbar(File,'Reposition...');
                        fprintf(oscilloscope,reposition_use);
                        fprintf(oscilloscope,position_query);
                        reposition_raw = fgetl2(oscilloscope);
                        reposition_check = str2double(reposition_raw);
                    end
                else
                    updateWaitbar(File,'Reposition...');
                    fprintf(oscilloscope,reposition_use);
                end
                fprintf('(%.2e)...',reposition);

%                 if ~isFineScale
                    fprintf('rescaling ')
                    if isTPS
                        fprintf(oscilloscope,'*CLS');
                        rescale_check = [];
                        while ~isequal(rescale_check,rescale)
                            updateWaitbar(File,'Rescaling...');
                            fprintf(oscilloscope,rescale_use);  % 200 mV/div vertical scale for voltage
                            fprintf(oscilloscope,scale_query);
                            rescale_raw = fgetl2(oscilloscope);
                            rescale_check = str2double(rescale_raw);
                        end
                    else
                        updateWaitbar(File,'Rescaling...');
                        fprintf(oscilloscope,rescale_use);  % 200 mV/div vertical scale for voltage
                    end
                    fprintf('(%.2e V)...',rescale);
%                 end
                
                [endTime,unit] = getEndTime(reViewTime);
                fprintf('OK (%.2f %s)\n',endTime,unit);
                fprintf('\t');
                File = getSettings(File,deviceNum,scopeChannel);
                isFineScale = false;
            else
                isInView = true;
            end
        else
            isInView = true;
        end
    end
    if fineScale_count > 1
%         if isTooSmall
%             fineScale_min = fineScale_min / 2;
%         else
% %         fineScale_min = fineScale_min + fineScale_add;
        fineScale_min = fineScale_min * 2;
%         end
    end
    fineScale_min = round(fineScale_min,3,'significant');

    if fineScale_count > 4 || isFineScaleUsed
        isInRange = true;
    end

    % ylim([range_min range_max]);
    % fig = figure(channelNum+1);
    % figure(fig);
    % plot(YUNits_monitor); hold on;
    % ylim('tight');
    % drawnow;

    varargout{1} = YUNits;
    varargout{2} = YUNits_raw;
    % fprintf(oscilloscope,'ACQuire:STAte RUN');

    % Adjust scaling
    if isAtScaleMin || ...
            (isScaleRepeated || isScaleUsed)
        isInRange = true;
    end
    if ~isInRange && hasAmplitude
        vertScale = [];

        % Scale
        if isTPS
            fprintf(oscilloscope,'*CLS');
        end
        while isempty(vertScale) || isnan(vertScale)
            fprintf(oscilloscope,scale_query);
            vertScale_raw = fgetl2(oscilloscope);
            vertScale = str2double(vertScale_raw);
            updateWaitbar(File);
        end

        % Position
        pos_factor = [];
        while isempty(pos_factor) || isnan(pos_factor)
            updateWaitbar(File,'Getting position...');
            fprintf(oscilloscope,position_query);
            pos_raw = fgetl2(oscilloscope);
            pos_factor = str2double(pos_raw);
        end

        pos_check = -pos_factor * vertScale;
        range_min_check = MAX_FACTOR * -vertScale + pos_check;
        range_max_check = MAX_FACTOR * vertScale + pos_check;
        inView_tf = isbetween2([YUNits_min YUNits_max],range_min_check,range_max_check);

        % Fine scaling
        isGetFine = ~isSymmetric || r2 > 0.80 || (~isBP && isReturnSmall);
            %|| (~isMathChannel && (YUNits_range < vertRangeMin || isReturnSmall));
        if isGetFine
            isCoarseScale = false;
            fractDiff = 0.5;
        else
            fractDiff = 0.1;
        end
        isTooSmall = ...
            (abs(range_min_check - YUNits_min) / range_min_check > fractDiff) || ...
            (abs(range_max_check - YUNits_max) / range_max_check > fractDiff);
        if fineScale_count > 1
            fineScale_arr_alloc = [fineScale_arr;scale_new];
            fineScale_arr = fineScale_arr_alloc;
        end
        isInView_check = all(inView_tf) && ~isTooSmall;
        if isFineScale && (isInView_check && isInView)
            fprintf('fine fit...');
            isInRange = true;
        end
        
%         if ~(isGetFine || isFineScale)
%             isCoarseScale = true;
%             isGetFine = false;
%             isFineScale = false;
%         else
%             isCoarseScale = false;
%         end

        if ~isInRange
            if ~isGetFine && (logical2(isCoarseScale) || isempty(isCoarseScale))
                % Adjust coarse scaling
                isCoarseScale = true;

                % Coarse scaling: maximize waveform visibility
                vertScale_idx = find(YUNits_range > vertRange,1) + 1;
                if vertScale_idx <= 0
                    vertScale_idx = 1;
                end
%                 targetScale = YUNits_range / 2 / MAX_FACTOR;
%                 % Find the closest available scale
%                 [~, vertScale_idx] = min(abs(vertScale_arr - targetScale));
                scale = vertScale_arr(vertScale_idx);
                vertScale_check_alloc = [vertScale_check;scale];
                vertScale_check = vertScale_check_alloc;
                vertScale_count(vertScale_idx) = vertScale_count(vertScale_idx) + 1;

                % Check repeats
                isScaleRepeated = any(vertScale_count > 1);
                isScaleUsed = sum(ismember(vertScale_check,scale)) > 1;
                if isScaleUsed || isScaleRepeated
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
                    % [endTime,unit] = getEndTime(startTime);
                    % fprintf('%.2e V (%.2f %s)\n',scale,endTime,unit);
                    % fprintf('OK (%.2f %s)\n',endTime,unit);
                end
            else
                if (isGetFine || isFineScale) && ~(logical2(isCoarseScale) || isempty(isCoarseScale))
                    % Adjust fine scaling
                    updateWaitbar(File,'Fine scaling and position...');
%                     if count > 1
                        fprintf('fine ');
%                     else
%                         fprintf('Fine ');
%                     end
                    [scale_new,~] = setFineScalePos(File, ...
                        scopeChannel,YUNits_min,YUNits_max,YUNits_mean,fineScale_min);
                    isFineScaleUsed = sum(ismember(fineScale_arr,scale_new)) > 1;
                    if isFineScaleUsed
                        isInRange = true;
                    end
                    isFineScale = true;
                    fineScale_count = fineScale_count + 1;
                    % [endTime,unit] = getEndTime(startTime);
                    %                 else
                    %                     % Adjust coarse scaling
                    %                     if ~(YUNits_range < vertRangeMin)
                    %                         fprintf('coarse...');
                    % %                         vertScale_count(vertScale_idx) = vertScale_count(vertScale_idx) + 1;
                    %                         if isMathChannel
                    %                             scale_use = sprintf('%s:VERtical:SCAle %.2e',scopeChannel,scale);
                    %                         else
                    %                             scale_use = sprintf('%s:SCAle %.2e',scopeChannel,scale);
                    %                         end
                    %                         if isTPS
                    %                             scale_check = [];
                    %                             while ~isequal(scale_check,scale)
                    %                                 fprintf(oscilloscope,scale_use);
                    %                                 fprintf(oscilloscope,scale_query);
                    %                                 scale_raw = fgetl2(oscilloscope);
                    %                                 scale_check = str2double(scale_raw);
                    %                             end
                    %                         else
                    %                             fprintf(oscilloscope,scale_use);
                    %                         end
                    %                         fprintf('%.2e V...',scale);
                    %                     else
                    %                         if isGetFine
                    %                             [scale_new,pos_new] = setFineScalePos(File, ...
                    %                                 scopeChannel,YUNits_min,YUNits_max,YUNits_brefore,fineScale_min);
                    %                             fineScale_count = fineScale_count + 1;
                    %                         else
                    %                             isFineScale = true;
                    %                         end
                    %                     end
                    %                     if scale == vertScale_min
                    %                         isInRange = true;
                    %                     end
                end
            end
        else
            % isInRange = true;
        end
        fprintf(oscilloscope,'ACQuire:STAte RUN');
        if ~isInRange
            [endTime,unit] = getEndTime(startTime);
            fprintf('OK (%.2f %s)\n',endTime,unit);
            fprintf('\t');
            File = getSettings(File,deviceNum,scopeChannel);
        else
            [endTime,unit] = getEndTime(startTime);
            fprintf('OK (%.2f %s)\n',endTime,unit);
        end
    else
        [endTime,unit] = getEndTime(startTime);
        fprintf('OK (%.2f %s)\n',endTime,unit);
        isInRange = true;
        % break;
    end
    fprintf(oscilloscope,'ACQuire:STAte RUN');
end
updateWaitbar(File,[channelName ' captured.']);
% figure(fig);
% hold off;
% drawnow;
% close(fig);

end