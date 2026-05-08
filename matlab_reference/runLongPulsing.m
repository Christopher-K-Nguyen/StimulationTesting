function [File,isQuit] = runLongPulsing(File)
close all;
close(findall(0,'type','figure'));
try
    delete(findall(groot));
catch
end
clc;
warning('off','all');
isQuit = false;
stopStimulation();
try
    %% Variables
    % Fields
    notebook = File.Notebook;
    subject = File.Subject;
    emailAddress = File.User.Email;
    phone = File.User.Phone;
    carrier = File.User.Carrier;
    saveFolder = File.Path;
    hasMessage = ~isempty(emailAddress) || (~isempty(phone) && ~isempty(carrier));

    % File
    if ~isempty(subject)
        expName = [notebook '_' subject];
    else
        expName = notebook;
    end
    folderName = expName;
    fileName = expName;
    file_zip = '';
    folderPath = fullfile(saveFolder,folderName);
    mkdir(folderPath);

    % Device
    deviceType = File.Parameters.Device;

    % Channels
    plexonChannel_arr = File.Parameters.Channels.Plexon;
    testChannel_arr = File.Parameters.Channels.Test;

    % Surface Area
    surfaceArea_arr = File.Parameters.SurfaceArea;
    numOfSurfaceArea = length(surfaceArea_arr);

    % Electrode
    activeElectrode = File.Parameters.WorkingElectrode.Type;
    refElectrode = File.Parameters.ReferenceElectrode.Type;
    returnElectrode = File.Parameters.CounterElectrode.Type;

    % Configuration
    channelGroup_mat = File.Test.Groups;
    [numOfGroups,~] = size(channelGroup_mat);
    % file_tif_cell = cell()

    % Oscillscopes
    numOfDevices = length(File.Oscilloscope);
    isUSB_tf = zeros(numOfDevices,1);
    for deviceNum = 1:numOfDevices
        resourceName = File.Oscilloscope(deviceNum).Resource;
        isUSB_tf(deviceNum) = contains2(resourceName,'usb');
    end
    fieldsList = vertcat(File.Oscilloscope(:).Fields);
    activeChannel_tf = containsi(fieldsList,{'act','work','pot'});
    diffChannel_tf = containsi(fieldsList,{'diff'});
    voltageChannel_tf = containsi(fieldsList,{'volt'});
    if any(activeChannel_tf)
        specialChannel_idx = find(activeChannel_tf);
    elseif any(diffChannel_tf)
        specialChannel_idx = find(diffChannel_tf);
    elseif any(voltageChannel_tf)
        specialChannel_idx = find(voltageChannel_tf);
    else
        specialChannel_idx = 1;
    end
    specialChannel = fieldsList(specialChannel_idx);
    isVoltageSpecial = contains2(specialChannel,'volt');
    hasReturnChannel = contains2(fieldsList,{'ret','count'});

    % Stimulation parameters
    amplitude1_arr = File.Parameters.Amplitude1;
    amplitude2_arr = File.Parameters.Amplitude2;
    amplitude1 = amplitude1_arr(1);
    amplitude2 = amplitude2_arr(1);
    phaseWidth1 = File.Parameters.PhaseWidth1;        % first phase pulse width
    interphaseDelay = File.Parameters.InterphaseDelay;% interphase delay
    phaseWidth2 = File.Parameters.PhaseWidth2;        % second phase pulse width
    stimRate = File.Parameters.StimulationRate;
    pattern = struct( ...
        'A1',amplitude1, ...
        'A2',amplitude2, ...
        'W1',phaseWidth1, ...
        'W2',phaseWidth2, ...
        'Delay',interphaseDelay);
    isPause = File.Test.Pause;

    % Pulsing
    pulsingTime = File.Test.Duration;
    numOfPulses = File.Test.NumberOfPulses;
    periodic = File.Test.Periodic;
    periodicTime = periodic / stimRate;
    pulsingTime_use = addCommas(pulsingTime);
    numOfPulses_use = addCommas(numOfPulses);
    barUpdate = 0.1;
    pulsingTime_arr = 0:barUpdate:pulsingTime;
    % pulseNum_arr = 0:numOfPulses;
    timestamp_arr = 0:periodicTime:pulsingTime;
    timestamp_len = length(timestamp_arr);
    numOfCaptures = numOfPulses / periodic + 1;
    numOfPlaces = floor(log10(numOfCaptures)) + 1;

    % Progress Bar
    pulsingProg = 0;
    progBar = waitbar(pulsingProg,'Seting up...','Name','Pulsing Progress');
    % progBar.CloseRequestFcn = '';
    File.Test.Progress = progBar;
    timestamp_idx = 1;
    time_idx = 1;
    captureNum = 0;

    % Status
    Status = struct( ...
        'Description','', ...
        'Good',true, ...
        'Normal',true, ...
        'PotentialLimit',0, ...
        'VoltageSafety',true, ...
        'VoltageCompliance',false, ...
        'MaxCurrent',false, ...
        'TooLong',false, ...
        'Quit',false);

    % Metrics
metricName_cell = {'Potential Excursion','Driving Voltage','Access Voltage','Access Resistance'};
if ~isVoltageSpecial || hasReturnChannel
    if ~isVoltageSpecial
        metricName_cell = [metricName_cell, ...
            'Active Driving Potential'];
    end
    if hasReturnChannel
        metricName_cell = [metricName_cell, ...
            'Return Driving Potential'];
    end    
end
if ~isVoltageSpecial || hasReturnChannel
    if ~isVoltageSpecial
        metricName_cell = [metricName_cell, ...
            'Active Potential Transient'];
    end
    if hasReturnChannel
        metricName_cell = [metricName_cell, ...
            'Return Potential Transient'];
    end
else
    metricName_cell = [metricName_cell, ...
        'Voltage Transient'];
end
numOfMetricNames = length(metricName_cell);
    % numOfFiles = numOfMetricNames * numOfGroups;
    % file_tif_cell = cell(numOfFiles);
    isOn = File.Stimulator.Status;
    if isOn
        expTime = tic;
    %% Prepare files
    savePulsingData( ...
        File, ...
        fileName, ...
        folderPath, ...
        true);

        %% Prepare stimulation
        % Initialize View
        File = setOscilloscopeStatus(File,'open');
        setOscillocopeView(File,amplitude1);

        % Set stimulation parameters
        for groupNum = 1:numOfGroups
            channelNum = channelGroup_mat(groupNum,1);
            isQuit = setPattern(File,channelNum,pattern);
            if isQuit
                error('setPattern');
            end
        end

        % Set stimulation rate
        isQuit = setAllStimRate(stimRate);
        if isQuit
            error('setAllStimRate');
        end

        % Set number of repetitions for all stimulators
        isQuit = setAllNumOfPulses(0);
        if isQuit
            error('setAllNumOfPulses');
        end
        fprintf('\n');

        % Load parameters to channel
        isQuit = loadPattern(File);
        if isQuit
            error('loadPattern');
        end

        %% Pulsing
        fprintf('Stimulation intensity:\n');

        % Amplitude
        fprintf('\tIstim = %.2f uA\n',amplitude1);

        % Charge
        if numOfSurfaceArea == 1
            surfaceArea = surfaceArea_arr;
            [chargePhase,chargeInjection] = getCharge( ...
                amplitude1,phaseWidth1,surfaceArea);
            fprintf('\tQph = %g nC/ph\n',chargePhase);
            fprintf('\tQinj = %g mC/cm2\n',chargeInjection);
        end

        % Start
        isQuit = startStimulation(File);
        if isQuit
            error('startStimulation');
        end
        fprintf('Starting stimulation for %s s\t(%s pulses)...\n',pulsingTime_use,numOfPulses_use);
        startTime = tic;
        File.Test.StartTime = startTime;
        isOver = false;
        while ~isOver
            presentTime = toc(startTime);
            pulsingProg = presentTime / pulsingTime;
            if pulsingProg > 1
                pulsingProg = 1;
            end
            presentTime_use = addCommas(presentTime);
            presentTime_round_use = addCommas(round(presentTime,1));
            if ~contains(presentTime_round_use,'.')
                presentTime_round_use = append(presentTime_round_use,'.0');
            end
            presentPulses = presentTime * stimRate;
            presentPulses_use = addCommas(presentPulses);
            presentPulses_round_use = addCommas(round(presentPulses));
            progMsg = sprintf('%s / %s s\n(%s / %s pulses)', ...
                presentTime_round_use,pulsingTime_use, ...
                presentPulses_round_use,numOfPulses_use);
            if presentTime >= pulsingTime_arr(time_idx)
                time_idx = time_idx + 1;
                waitbar(pulsingProg,progBar,progMsg);
            end
            if presentTime >= pulsingTime
                isOver = true;
            else
                timestamp = timestamp_arr(timestamp_idx);
                % pulseNum = pulseNum_arr(timestamp_idx);
                pulseNum = timestamp * stimRate;
                pulseNum_use = addCommas(pulseNum);
                if presentTime >= timestamp && timestamp_idx < timestamp_len
                    timestamp_idx = timestamp_idx + 1;
                    if ~contains(presentTime_use,'.')
                        presentTime_use = sprintf('%s.0000',presentTime_use);
                    end
                    decimal_idx = strfind(presentPulses_use,'.');
                    decimal = presentPulses_use(decimal_idx+1:end);
                    decimal_len = length(decimal);
                    if ~decimal_len < 4
                        len_diff = 4 - decimal_len;
                        switch len_diff
                            case 1
                                presentPulses_use = sprintf('%s0',presentPulses_use);
                            case 2
                                presentPulses_use = sprintf('%s00',presentPulses_use);
                            case 3
                                presentPulses_use = sprintf('%s000',presentPulses_use);
                        end
                    end
                    fprintf('Time elapsed: %s s\t\t(%s pulses)\n',presentTime_use,presentPulses_use);

                    %% Capture
                    captureNum  = captureNum + 1;
                    fprintf('Pulse Number: %s\n',pulseNum_use);
                    idx_num = captureNum - 1;
                    periodicFileName = eval(sprintf("sprintf('%%s_%%0%ddx%%.1e',fileName,idx_num,periodic)",numOfPlaces));
                    periodicFileName = erase(periodicFileName,{'+0','.0'});
                    captureTime = tic;
                    for groupNum = 1:numOfGroups % consecutively stimulate channels
                        % Channel connection
                        channelNum = channelGroup_mat(groupNum,1);
                        % plexonChannel = plexonChannel_arr(channelNum);
                        channel_tf = testChannel_arr == channelNum;
                        fprintf('%s Channel %d...',deviceType,channelNum);
                        channelStim = plexonChannel_arr(channelNum);
                        fprintf('Plexon Channel %d\n',channelStim);
                        File.Data(groupNum).ActiveChannel = channelNum;
                        if numOfSurfaceArea > 1
                            surfaceArea = surfaceArea_arr(channel_tf);
                        else
                            surfaceArea = surfaceArea_arr;
                        end
                        File.Data(groupNum).SurfaceArea = surfaceArea;

                        % Set the monitor channel
                        isQuit = setMonitorChannel(File,channelNum);
                        if isQuit
                            error('setMonitorChannel');
                        end
                        % captureFract = (groupNum - 1) / numOfGroups;
                        % capturePercent = captureFract * 100;
                        % captureMsg = sprintf('Channel Capture: %d / %d (%.2f%%)', ...
                        %     groupNum-1,numOfGroups,capturePercent);
                        % % channelTitle_msg = [channelName '...'];
                        % progMsg_cell = {progMsg,captureMsg,channelTitle_msg};
                        % waitbar(pulsingProg,progBar,progMsg_cell);

                        startChannelTime = tic;
                        File.Data(groupNum).Capture(captureNum).Index = captureNum;
                        File.Data(groupNum).Capture(captureNum).PulseNumber = pulseNum;
                        File.Data(groupNum).Capture(captureNum).Status = Status;
                        File.Data(groupNum).Capture(captureNum).Amplitude = amplitude1;
                        File.Data(groupNum).Capture(captureNum).PhaseWidth = phaseWidth1;
                        [chargePhase,chargeInjection] = getCharge( ...
                            amplitude1,phaseWidth1,surfaceArea);
                        File.Data(groupNum).Capture(captureNum).ChargePhase = chargePhase;
                        File.Data(groupNum).Capture(captureNum).ChargeInjection = chargeInjection;

                        % Electrode
                        File.Data(groupNum).Active = activeElectrode;
                        File.Data(groupNum).Return = returnElectrode;
                        File.Data(groupNum).Reference = refElectrode;
                        if isVoltageSpecial
                            electrode = refElectrode;
                        else
                            electrode = returnElectrode;
                        end
                        refElectrode_use = sprintf('%s in electrolyte',electrode);
                        File.Data(groupNum).Reference = refElectrode_use;

                        % Capture waveform
                        %                 pause(3);
                        dateTime = getDateTime();
                        File.Data(groupNum).Capture(captureNum). ...
                            DateTime = dateTime;    % date and time completed
                        [File,buttonHandle] = getWaveformData(File);
                        updateWaitbar(File);
                        File.Data(groupNum).Capture(captureNum).Status.Good = true;
                        isQuit = File.Data(groupNum).Capture(captureNum).Status.Quit;
                        if isQuit  	% stop by button handle
                            fprintf('OK\n\n');
                            break;
                        end

                        %% Store data in structure
                        % Capture
                        File.Data(groupNum).Capture(captureNum). ...
                            Amplitude = amplitude1;
                        File.Data(groupNum).Capture(captureNum). ...
                            ChargePhase = chargePhase;       	    % charge per phase
                        File.Data(groupNum).Capture(captureNum). ...
                            ChargeInjection = chargeInjection;
                        % Group Number
                        File.Data(groupNum).Amplitude = amplitude1;
                        File.Data(groupNum).ChargePhase = chargePhase;       	    % charge per phase
                        File.Data(groupNum).ChargeInjection = chargeInjection;
                        % Date
                        dateTime = getDateTime();
                        File.Data(groupNum).Capture(captureNum).DateTime = dateTime; % date and time completed
                        File.Data(groupNum).DateTime = dateTime;
                        if isgraphics(buttonHandle)
                            if ishandle(buttonHandle)
                                try
                                    buttonHandle.Visible = 'off';
                                    delete(buttonHandle);
                                catch
                                    delete(buttonHandle);
                                end
                                % File.Data(groupNum).Figure = figure(groupNum);
                            end
                        end
                        elapsedChannelTime = toc(startChannelTime);
                        File.Data(groupNum).TimeElapased = elapsedChannelTime;
                        status = File.Data(groupNum).Capture(captureNum).Status.Description;
                        File.Data(groupNum).Status = status;
                        % Save figure
                        if ishandle(figure(groupNum))
                            file_tif = saveChannelFigure( ...
                                File, ...
                                periodicFileName, ...
                                folderPath);
                            updateWaitbar(File);
                            fprintf('\n');
                        else
                            file_tif = [];
                        end
                        % file_tif_cell{groupNum} = file_tif;
                    end
                    if isOver
                        stopStimulation();
                    end

                    %% Saving files
                    % Perioidc
                    dateTimeModified = getDateTime();
                    File.DateTimeModified = dateTimeModified;
                    [periodicFile_mat,periodicFile_xlsx] = saveVoltageTransientData( ...
                        File, ...
                        periodicFileName, ...
                        folderPath);
                    % Pulsing
                    for groupNum = 1:numOfGroups % consecutively stimulate channels
                        % Channel connection
                        channelNum = channelGroup_mat(groupNum,1);
                        channelName = sprintf('Channel %02d',channelNum);
                        getPulsingPlot(File,channelNum);
                        if ishandle(figure(groupNum))
                            fprintf('\n');
                        else
                            % file_tif = [];
                        end
                    end
                    [file_mat,file_xlsx] = savePulsingData( ...
                        File, ...
                        fileName, ...
                        folderPath);
                    updateWaitbar(File);
                    fprintf('\n');
                    [endTime,unit] = getEndTime(captureTime);
                    fprintf('Capture time elapsed: %.2f %s\n',endTime,unit);
                    fprintf('\n');
                    
                    %% Pause
                    if isPause && ~isOver
                        stopStimulation();
                        startPause = tic;
                        if hasMessage
                            dirFile = dir(folderPath);
                            dirFileByte_arr = [dirFile(:).bytes];
                            dirFileByte_max = sum(dirFileByte_arr);
                            dirFileMByte_max = dirFileByte_max / 2^20;
                            isFileSmall = false;
                            if dirFileMByte_max < 25
                                isFileSmall = true;
                                files_cell = {periodicFile_xlsx;periodicFile_mat};
                            else
                                dirFile_xlsx = dir(periodicFile_xlsx);
                                dirFile_mat = dir(periodicFile_mat);
                                dirFileByte = dirFile_xlsx.bytes + dirFile_mat.bytes;
                                dirFileMByte = dirFileByte / 2^20;
                                if dirFileMByte < 25
                                    isFileSmall = true;
                                    files_cell = {periodicFile_xlsx;periodicFile_mat};
                                end
                            end
                            if isFileSmall
                                zipFilename = [fileName '.zip'];       	% fileName for .zip file
                                file_zip = fullfile(folderPath,zipFilename);  % save path for .zip file
                                fprintf('Compressing files...');
                                startCompressTime = tic;
                                zip(file_zip,files_cell)
                                attachements = file_zip;
                                [endCompressTime,unit] = getEndTime(startCompressTime);
                                fprintf('%s (%.2f %s)\n',zipFilename,endCompressTime,unit);
                            else
                                file_zip = [];
                                attachements = periodicFile_mat;
                            end
                            [endTime,unit] = getEndTime(startTime);
                            emailSubject = sprintf('%s Pulsing: %s / %s',fileName,pulseNum_use,numOfPulses_use);
                            sendMessage(File,emailSubject,attachements,endTime,unit,emailSubject);
                        end
                        if ~isempty(file_zip)
                            startDeleteTime = tic;
                            fprintf('Deleting %s...',zipFilename);
                            delete(file_zip);
                            [endDeleteTime,unit] = getEndTime(startDeleteTime);
                            fprintf('OK (%.2f %s)\n',endDeleteTime,unit);
                        end
                        msg = sprintf('Paused at %s / %s',pulseNum_use,numOfPulses_use);
                        questPrompt = {msg,'Do you want to end pause?'};
                        confirmContinue = false;
                        while ~confirmContinue
                            fprintf('Paused...');
                            questContinue = questdlg(questPrompt, ...
                                'Paused', ...
                                BUTTON_YES,BUTTON_NO,BUTTON_YES);

                            promptQuestExpType = sprintf('Continue? {\\bf%s}',questContinue);
                            opts.Default = BUTTON_CONFIRM;
                            % Question box
                            questConfirmPause = questdlg(...                   % question dialog
                                promptQuestExpType,...                    % question prompts
                                'Confirm Pausing',...                     % question title
                                BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,... % buttons
                                opts);                                      % dialog options
                            % Confirmation
                            switch questConfirmPause                       % apply choice
                                case BUTTON_CONFIRM                     % check confirmation
                                    switch questContinue
                                        case BUTTON_YES
                                            isContinue = true;
                                        case BUTTON_NO
                                            isContinue = false;
                                    end
                                    confirmContinue = isContinue;               % confirm parameters
                                    fprintf('%s\n',questContinue);% parameters confirmed
                                case BUTTON_TRY                         % try again
                                    confirmContinue = false;               % trying again
                                    fprintf('Trying again...\n');     % starting over
                                case BUTTON_CANCEL                      % quit
                                    fprintf('Quitting...\n\n');         % quitting
                                    isQuit = true;
                                    break;                             % exit program
                                otherwise                               % cancel
                                    fprintf('Quitting...\n\n');         % quitting
                                    isQuit = true;
                                    break;                             % exit program
                            end
                            if isQuit
                                return;
                            end
                        end
                        endPause = toc(startPause);
                        startTime = startTime + endPause;
                        startStimulation();
                    end
                end
            end
        end
        if isOver
            stopStimulation();
            delete(progBar);
            File.Test.Progress = [];
        end
        File = setOscilloscopeStatus(File,'close');
        %% Email
        if hasMessage
            dirFile = dir(folderPath);
            dirFileByte_arr = [dirFile(:).bytes];
            dirFileByte_max = sum(dirFileByte_arr);
            dirFileMByte_max = dirFileByte_max / 2^20;
            isFileSmall = false;
            if dirFileMByte_max < 25
                isFileSmall = true;
                files_cell = {file_mat;file_xlsx};
            else
                dirFile_xlsx = dir(periodicFile_xlsx);
                dirFile_mat = dir(periodicFile_mat);
                dirFileByte = dirFile_xlsx.bytes + dirFile_mat.bytes;
                dirFileMByte = dirFileByte / 2^20;
                if dirFileMByte < 25
                    isFileSmall = true;
                    files_cell = {file_mat;file_xlsx};
                end
            end
            if isFileSmall
                zipFilename = [fileName '.zip'];       	% fileName for .zip file
                file_zip = fullfile(folderPath,zipFilename);  % save path for .zip file
                fprintf('Compressing files...');
                startCompressTime = tic;
                zip(file_zip,files_cell)
                attachements = file_zip;
                [endCompressTime,unit] = getEndTime(startCompressTime);
                fprintf('%s (%.2f %s)\n',zipFilename,endCompressTime,unit);
            else
                file_zip = [];
                attachements = [periodicFile_mat;file_tif_cell];
            end

            [endTime,unit] = getEndTime(startTime);
            emailSubject = sprintf('%s Pulsing Complete',fileName);
            sendMessage(File,emailSubject,attachements,endTime,unit);
        end
        if ~isempty(file_zip)
            startDeleteTime = tic;
            fprintf('Deleting %s...',zipFilename);
            delete(file_zip);
            [endDeleteTime,unit] = getEndTime(startDeleteTime);
            fprintf('OK (%.2f %s)\n',endDeleteTime,unit);
        end
        if isQuit
            return;
        end
    end
    File.Test.Progress = [];

catch err
    fprintf('\n');
    stopStimulation();
    beep;pause(0.5);
    beep;pause(0.5);
    beep;
    File = setOscilloscopeStatus(File,'close');
    fields_check = fieldnames(File);
    if contains2(fields_check,'Error')
        idx = 2;
    else
        idx = 1;
    end
    File.Error(idx).Status = err;
    report = getReport(err);
    report_short = getReport(err,'extended','hyperlinks','off');
    File.Error(idx).Report = report;
    display(report);

    varName = getVarName(File);
    newVarName = ['FailedFile_' fileName];
    setNewVarName = sprintf('%s = %s;',newVarName,varName);
    eval(setNewVarName);
    fileName_mat = [fileName '.mat'];       	% fileName for .mat file
    tempName_mat = [fileName '_temp.mat'];
    filePath_mat = fullfile(folderPath,fileName_mat);  % save path for .mat file
    tempPath_mat = fullfile(folderPath,tempName_mat);
    attachements = filePath_mat;
    try
        save(filePath_mat,newVarName);             	% save .mat file
    catch
        save(tempPath_mat,newVarName);
        delete(filePath_mat);
        movefile(tempPath_mat,filePath_mat,'f');
    end
    emailSubject = sprintf('MATLAB VT Failed: %s',fileName);
    [endTime,unit] = getEndTime(expTime);
    sendError(File,emailSubject,report_short,attachements,endTime,unit);

    isQuit = true;
end
fprintf('\n');

end