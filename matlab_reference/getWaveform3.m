function varargout = getWaveform3(File,varargin)
%% Constants
SCALE_MIN = 2e-3;
CURRENT_RANGE_MIN = 40;
ASYMM_CHECK = 3;
SCREEN_FACTOR = 4;
MAX_FACTOR = 4.99;

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
    amplitude = File.Data(groupNum).Capture(captureNum).Amplitude;
    phaseWidth = File.Data(groupNum).Capture(captureNum).PhaseWidth;

    % Time
    time = File.Data(groupNum).Capture(captureNum).Time;

    % Fine Scale
    scale_min_use = SCALE_MIN;
else
    digitalDelay = File.Stimulator.DigitalDelay;
    [File,time_raw] = getTime(File);
    time = time_raw * 1e6 - digitalDelay;
    amplitude = 1;
    phaseWidth = 1;
    scale_min_use = SCALE_MIN * 2.5;
end
polarity = File.Parameters.Polarity;
isSymmetric = File.Parameters.Symmetry;
hasAmplitude = any(amplitude);
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
isLimitReached = File.Data(groupNum).Capture(captureNum).Status.PotentialLimit;

% Test Type
testType = File.Test.ID;
isTargetMax = contains2(testType,'max');
targetCharge = File.Test.ChargePhase;
targetAmplitude = roundStim(targetCharge / phaseWidth * 1e3);
isAtFixedTarget = abs(amplitude) == targetAmplitude;

% Vertical scale
vertScale_start1 = 2;
vertScale_diff1 = 0.5;
vertScale_start2 = 5;
vertScale_diff2 = 1;
vertScale_start3 = 20;
vertScale_diff3 = 5;
vertScale_start4 = 100;
vertScale_diff4 = 20;
vertScale_start5 = 300;
vertScale_diff5 = 50;
vertScale_start6= 1e3;
vertScale_diff6 = 100;

vertScale_end1 = vertScale_start2 - vertScale_diff1;
vertScale_end2 = vertScale_start3 - vertScale_diff2;
vertScale_end3 = vertScale_start4 - vertScale_diff3;
vertScale_end4 = vertScale_start5 - vertScale_diff4;
vertScale_end5 = vertScale_start6 - vertScale_diff5;

vertScale_part1 = vertScale_start1:vertScale_diff1:vertScale_end1;
vertScale_part2 = vertScale_start2:vertScale_diff2:vertScale_end2;
vertScale_part3 = vertScale_start3:vertScale_diff3:vertScale_end3;
vertScale_part4 = vertScale_start4:vertScale_diff4:vertScale_end4;
vertScale_part5 = vertScale_start5:vertScale_diff5:vertScale_end5;
vertScale_part6 = vertScale_start6:vertScale_diff6:5e3;

vertScale_arr = [ ....
    vertScale_part1, ...
    vertScale_part2, ...
    vertScale_part3, ...
    vertScale_part4, ...
    vertScale_part5, ...
    vertScale_part6] * 1e-3;
numOfScales = length(vertScale_arr);
vertScale_min = 2e-3;

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
isReturnSmall = isReturnChannel && (isMP || isPartial || isCG);
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

% Measurement Source
source_use = ['MEASUrement:IMMed:SOUrce1 ' scopeChannel];
fprintf(oscilloscope,source_use);

% Initialization
try
    offset = File.Stimulator.Offset(groupNum).(scopeChannel);
catch
    offset = [];
end
dataLength = File.Oscilloscope(deviceNum).Settings.DataLength;
vertScale_count = zeros(numOfScales,1);
vertScale_check = [];
fineScale_arr = [];
isInRange = false;
isScaleRepeated = false;
isScaleUsed = false;
isInView_check = false;
isAtScaleMin = false;
isGetCoarse = (isVoltageChannel || isBP || ((isTargetMax || ~isAtFixedTarget) && isTP)) && isSymmetric;
isGetFine = false;
isFineScale = false;
isCoarseScale = [];
coarseScale_count = 0;
fineScale_count = 0;
scale_new = [];
isReduceNeeded_count = 0;

% %% Reset View
% for channel_idx = 1:4
%     scopeChannel_test = sprintf('CH%d',channel_idx);
%     fprintf('Resetting %s for display...',scopeChannel_test);
%     startTime = tic;
%     status_use = sprintf('SELect:%s OFF',scopeChannel_test);
%     if isTPS
%         status = '1';
%         status_check = '';
%         status_query = sprintf('SELect:%s?',scopeChannel_test);
%         while ~contains2(status_check,status)
%             fprintf(oscilloscope,status_use);
%             fprintf(oscilloscope,status_query);
%             status_check = fgetl2(oscilloscope);
%         end
%     else
%         fprintf(oscilloscope,status_use);
%     end
%     [endTime,unit] = getEndTime(startTime);
%     fprintf('OFF (%.2f %s)\n',endTime,unit);
% end
% 
% fprintf('Setting %s for display...',scopeChannel);
% startTime = tic;
% status_use = sprintf('SELect:%s ON',scopeChannel);
% if isTPS
%     status = '1';
%     status_check = '';
%     status_query = sprintf('SELect:%s?',scopeChannel);
%     while ~contains2(status_check,status)
%         fprintf(oscilloscope,status_use);
%         fprintf(oscilloscope,status_query);
%         status_check = fgetl2(oscilloscope);
%     end
% else
%     fprintf(oscilloscope,status_use);
% end
% [endTime,unit] = getEndTime(startTime);
% fprintf('ON (%.2f %s)\n',endTime,unit);

%% Capture Waveform
if ~(isMP || isTP || isPartial || isCG)
    fprintf(oscilloscope,'*CLS');
    pos_query = sprintf('%s:POSition?',scopeChannel);
    % fprintf(oscilloscope,pos_query);
    % pos_raw = fgetl2(oscilloscope);
    % pos = round(str2double(pos_raw),2,'significant');
    pos_fix = 0;
    pos_use = sprintf('%s:POSition %.2e',scopeChannel,pos_fix);
    % if isequal(pos,pos_fix)
        if isTPS
            pos_check = [];
            while ~isequal(pos_check,pos_fix)
                fprintf(oscilloscope,pos_use);
                fprintf(oscilloscope,pos_query);
                pos_raw = fgetl2(oscilloscope);
                pos_check = str2double(pos_raw);
            end
        else
            fprintf(oscilloscope,pos_use);
        end
    % end
end

% fprintf(oscilloscope,'ACQuire:MODe SAMple');
% fprintf(oscilloscope,'ACQuire:MODe PEAKdetect');
% fprintf(oscilloscope,'ACQuire:MODe AVErage');
numOfAvg_arr = [4 16 64 128];
numOfAvg_idx = 1;
numOfAvg = numOfAvg_arr(numOfAvg_idx);
acq_use = sprintf('ACQuire:NUMAVg %d',numOfAvg);
fprintf(oscilloscope,acq_use);
cursorTime = tic;
fprintf('\tSetting cursor source...');
cursorSource = sprintf('CURSor:SELect:SOUrce %s',scopeChannel);
fprintf(oscilloscope,cursorSource);
[endTime,unit] = getEndTime(cursorTime);
fprintf('%s \t\t(%.2f %s)\n',scopeChannel,endTime,unit);
% fprintf(oscilloscope,'*CLS');
count = 0;
while ~isInRange
    % fprintf('\t%s %s %s...',model,scopeChannel,channelName);
    startTime = tic;
    count = count + 1;
    
    updateWaitbar(File);
    % Measurements
    fprintf('\tMeasuring cursors...');
    % fprintf(oscilloscope,'ACQuire:STAte RUN');
    updateWaitbar(File,'Measuring peaks....');

    cursorVal_arr = zeros(2,1);
    for cursorNum = 1:2
        % if isFineScale
            numOfAcq = 1;
        % else
            % numOfAcq = 2;
        % end
        numOfAcq_check = 0;
        cursor_check = [];
        while isempty(cursor_check)
            % captureTime = tic;
            % while toc(captureTime) < 0.1
            % end
            while numOfAcq_check < numOfAcq
                fprintf(oscilloscope,'ACQuire:NUMACq?');
                numOfAcq_raw = fgetl2(oscilloscope);
                numOfAcq_check = str2double(numOfAcq_raw);
            end
            cursor_query = sprintf('CURSor:VBArs:HPOS%d?',cursorNum);
            fprintf(oscilloscope,cursor_query);
            cursor_raw = fgetl2(oscilloscope);
            cursor_check = str2double(cursor_raw);
            if abs(cursor_check) > 20
                cursor_check = [];
                numOfAcq = numOfAcq + 1;
                if numOfAcq > numOfAvg
                    numOfAvg_idx = numOfAvg_idx + 1;
                    numOfAvg = numOfAvg_arr(numOfAvg_idx);
                    if numOfAvg > max(numOfAvg_arr)
                        numOfAvg = max(numOfAvg_arr);
                    end
                    acq_use = sprintf('ACQuire:NUMAVg %d',numOfAvg);
                    fprintf(oscilloscope,acq_use);
                end
            end
            fprintf(oscilloscope,'ACQuire:STAte RUN');
        end
        cursorVal_arr(cursorNum) = cursor_check;
        fprintf('%d (%.3f V)...',cursorNum,cursor_check);
    end
    min_val = min(cursorVal_arr);
    max_val = max(cursorVal_arr);

    % YUNits_range = max_val - min_val;
    min_round = round(min_val,3,'significant');
    max_round = round(max_val,3,'significant');
    isTooSmall = min_round == max_round;

    if fineScale_count == 0
        YUNits_min = min_val;  % min
        YUNits_max = max_val;  % max
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

    % if fineScale_count > 2
    %     isInRange = true;
    % end

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


        vertPos = -pos_factor * vertScale;
        range_min_check = MAX_FACTOR * -vertScale + vertPos;
        range_max_check = MAX_FACTOR * vertScale + vertPos;
        range_check = range_max_check - range_min_check;
        inView_tf = isbetween2(...
            [min_val max_val],...
            range_min_check,range_max_check, ...
            'open');
        % viewCheck = sum(inView_tf) / dataLength
        % isInView = viewCheck > 0.99;
        isInView = all(inView_tf);
        if scale_new == 2e-3
            isTooTight = false;
        else
            isTooTight = (max_val - min_val) / range_check < 0.2;
        end
        isInView_check = isInView; %
        

        % Fine scaling
        isGetFine = ~isGetCoarse && ...
            (~isSymmetric || isTooSmall || isTooTight || isReturnSmall || any(isLimitReached) || isAtFixedTarget);
        %|| (~isMathChannel && (YUNits_range < vertRangeMin || isReturnSmall));
        if isGetFine
            isCoarseScale = false;
            if isFineScale && isInView_check && ~isTooTight
                fprintf('fine fit...');
                isInRange = true;
            end

            if fineScale_count > 1
                fineScale_arr_alloc = [fineScale_arr;scale_new];
                fineScale_arr = fineScale_arr_alloc;
            end
        end

        if isCurrentSymmetric
            isInRange = true;
        end
        if ~isInRange
            if (isGetCoarse || ~isGetFine) && ...
                    (logical2(isCoarseScale) || isempty(isCoarseScale))
                % Adjust coarse scaling
                isCoarseScale = true;
                coarseScale_count = coarseScale_count + 1;

                % Coarse scaling: maximize waveform visibility
                scale_check = max(abs([min_val max_val])) / SCREEN_FACTOR;
                vertScale_idx = find(vertScale_arr > scale_check,1);
                if ~isInView_check
                    isReduceNeeded_count = isReduceNeeded_count + 1;
                    vertScale_idx = vertScale_idx + 10 * isReduceNeeded_count;
                    isReduceNeeded = true;
                else
                    isReduceNeeded = false;
                end
                if any(vertScale_idx < 1) ...
                        || any(isnan(vertScale_idx)) ...
                        || isempty(vertScale_idx)
                    numOfCoarseCheck = length(vertScale_check);
                    if numOfCoarseCheck > 0
                        lastCoarseScale = vertScale_check(numOfCoarseCheck);
                        vertScale_idx = find(vertScale_arr == lastCoarseScale,1);
                    end
                elseif any(vertScale_idx > numOfScales)
                    vertScale_idx = numOfScales;
                end
                scale = vertScale_arr(vertScale_idx);
                % len = length(vertScale_check);
                % if len > 0
                %     scale_prev = vertScale_check(len);
                %     if scale > scale_prev
                %         vertScale_idx = find(vertScale_arr == scale_prev,1);
                %         scale = vertScale_arr(vertScale_idx);
                %     end
                % end
                vertScale_check_alloc = [vertScale_check;scale];
                vertScale_check = vertScale_check_alloc;
                vertScale_count(vertScale_idx) = vertScale_count(vertScale_idx);

                % Check repeats
                isScaleRepeated = any(vertScale_count > 1);
                isScaleUsed = any(sum(ismember(vertScale_check,scale)) > 1);

                if (isScaleUsed || isScaleRepeated) %&& isInView_check
                    fprintf('coarse repeated...');
                    isInRange = true;
                elseif scale == vertScale_min
                    fprintf('min coarse...');
                    isInRange = true;
                else
                    fprintf('coarse ');
                    if isReduceNeeded
                        fprintf('(%.3f V to %.3f V)...',min_val,max_val);
                        fprintf('resizing ');
                    else
                        fprintf('scaling ');
                    end
                    if isMathChannel
                        scale_use = sprintf('%s:VERtical:SCAle %.2e',scopeChannel,scale);
                    else
                        scale_use = sprintf('%s:SCAle %.2e',scopeChannel,scale);
                    end
                    % while any(isnan(scale)) || any(isempty(scale))
                    %     len = len - 1;
                    %     fprintf('\n');
                    %     display(vertScale_idx);
                    %     display(scale);
                    % end
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
                    fprintf('(%.3f V)...',scale);
                end
            else
                if (isGetFine || isFineScale) %&& ~(logical2(isCoarseScale) || isempty(isCoarseScale))
                    % Adjust fine scaling
                    updateWaitbar(File,'Fine scaling and position...');
                    fprintf('fine ');
                    if isTooSmall
                        min_test = YUNits_min;
                        max_test = YUNits_max;
                        % if fineScale_count > 0
                        %     fineScale_count = fineScale_count + 1;
                        % end
                    else
                    %     if isTooTight
                    %         fineScale_count = fineScale_count + 1;
                    %     end
                        min_test = min_val;
                        max_test = max_val;
                    end
                    [scale_new,~] = setFineScalePos2(File, ...
                        scopeChannel, ...
                        min_test,max_test, ...
                        fineScale_count);
                    isFineScale = true;
                    fineScale_count = fineScale_count + 1;
                    if isempty(scale_new)
                        isInRange = true;
                    end
                end
            end
        else
            % isInRange = true;
        end
        % fprintf(oscilloscope,'ACQuire:STAte RUN');
    else
        isInRange = true;
        % break;
    end
    [endTime,unit] = getEndTime(startTime);
    fprintf('OK \t\t(%.2f %s)\n',endTime,unit);
    % fprintf(oscilloscope,'ACQuire:STAte RUN');
end
updateWaitbar(File,[channelName ' fit.']);

% Settings
fprintf(oscilloscope,'ACQuire:MODe AVErage');
fprintf(oscilloscope,'ACQuire:NUMAVg 128');
fprintf(oscilloscope,'ACQuire:STAte RUN');
fprintf('\t');
File = getSettings(File,deviceNum,scopeChannel);

% Waveform
fprintf('\tCapturing waveform...');
updateWaitbar(File,'Capturing waveform....');
startTime = tic;
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

YUNits = smooth(YUNits_fix); % 7

if isReturnChannel && isGetFull
    YUNits_prepulse = mean(YUNits(time < 0));
    setArduinoVoltage(File,YUNits_prepulse);
end

[endTime,unit] = getEndTime(startTime);
fprintf('OK \t\t(%.2f %s)\n',endTime,unit);
updateWaitbar(File,[channelName ' captured.']);

varargout{1} = YUNits;
varargout{2} = YUNits_raw;

end