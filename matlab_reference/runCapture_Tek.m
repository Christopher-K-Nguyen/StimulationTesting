function [File,quitProgram] = runCapture_Tek(File)
close all;
warning('off','all');
try
    %% Constants
    N_TO_MICRO = 1e6;
    MILLI_TO_N = 1e-3;

    %% Variables
    numOfDevices = length(File.Oscilloscope);
    subject = File.Subject;
    filepath = File.Path;
    dateTimeCreated = getDateTime();
    File.DateTimeCreated = dateTimeCreated;
    attachments_tif = {};
    attachments = {};

    %% Capture
    startTime = tic;
    quitProgram = false;
    while ~quitProgram
        % Index
        [File,quitProgram] = getIndex_Tek(File);
        if quitProgram
            break;
        end

        % Info
        File = getIndexInfo_Tek(File);

        confirmCapture = false;
        while ~confirmCapture
            startCaptureTime = tic;
            capture_arr = [File.Data.Index];
            captureNum = length(capture_arr);
            for deviceNum = 1:numOfDevices
                setStatus_Tek(File,deviceNum,'open');
                channelSelect_cell = File.Oscilloscope(deviceNum).Channels;
                channelName_cell = File.Oscilloscope(deviceNum).ChannelNames;
                numOfChannels = File.Oscilloscope(deviceNum).NumberOfChannels;
                if deviceNum == 1
                    time = getTime_Tek(File,deviceNum) * N_TO_MICRO;
                    File.Data(captureNum).Time = time;
                end
                for channel_idx = 1:numOfChannels
                    channel = channelSelect_cell{channel_idx};
                    channelName = channelName_cell{channel_idx};
                    [data,~] = getWaveform_Tek(File,deviceNum,channel);
                    if numOfDevices == 2 && deviceNum == 1
                        switch channel_idx
                            case 1
                                channelField = 'Voltage';
                            case 2
                                channelField = 'Current';
                        end
                    else
                        channelField = channelSelect_cell{channel_idx};
                    end
                    File.Data(captureNum).(channelField) = data;
                    if contains2(channelName,'curr')
                        surfaceArea = File.Data(captureNum).SurfaceArea;
                        currentDensity = getCurrentDensity(data,surfaceArea) * MILLI_TO_N;
                        File.Data(captureNum).CurrentDensity = currentDensity;
                    end
                end
            end
            [endCaptureTime,unit] = getEndTime(startCaptureTime);
            fprintf('Time Elapse: %.2f %s\n',endCaptureTime,unit);
            [File,fig] = getPlot_Tek(File);
            File.Data(captureNum).DateTime = getDateTime();
            File.Data(captureNum).Figure = fig;
        
            [confirmCapture,isSave] = getSave_Tek();
            if ~confirmCapture && ~isSave
                continue;
            elseif confirmCapture && ~isSave
                break;
            end
        end

        if quitProgram
            break;
        end

        if isSave
            [filepath_mat,filepath_csv,filepath_xlsx] = saveData_Tek(File);
            attachments_alloc = [attachments,filepath_csv];
            attachments = attachments_alloc;
            filepath_tif = saveFig_Tek(File);
            attachments_tif_alloc = [attachments_tif,filepath_tif];
            attachments_tif = attachments_tif_alloc;
            fprintf('\n');
        end
    end
    
    if ~isempty(attachments)
        [endTime,unit] = getEndTime(startTime);
        endTime_cell = {endTime,unit};
        attachments = [attachments_tif,filepath_mat,filepath_xlsx];
        filename_zip = [subject '.zip'];       	% filename for .zip file
        filepath_zip = fullfile(filepath,filename_zip);  % save path for .zip file
        try
            zip(filepath_zip,attachments);
        catch
            attachments = {filepath_mat,filepath_xlsx};
            zip(filepath_zip,attachments);
        end
    
        emailAddress = File.Email;
        emailSubject = [subject ' ' dateTimeCreated];
        sendEmail2( ...
            emailAddress, ...
            emailSubject, ...
            filepath_zip, ...
            endTime_cell);
    end

catch err
    beep;pause(0.5);beep;pause(0.5);beep;
    File.Error.Status = err;
    report = getReport(err);
    File.Error.Report = report;
    display(report);
    quitProgram = true;
end

end