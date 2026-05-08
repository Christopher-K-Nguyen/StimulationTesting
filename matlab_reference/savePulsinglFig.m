function [] = savePulsinglFig(...
    name,...
    stepNum,...
    savePath,...
    vtPlot)

%% Function
fprintf('Saving figure...');

% Save figure
if ishandle(vtPlot)
    filename_fig_char = sprintf('%s_Step%06d.tiff',name,stepNum);  % filename for .tif file (char)
    filename_fig = convertCharsToStrings(filename_fig_char);             	% filename string for .tif file (string)
    filename_tif = fullfile(savePath,filename_fig);
    print(vtPlot,'-dtiff',filename_tif,'-r300');
    fprintf('%s\n',filename_fig);   % .tif file saved
end
% fprintf('All files saved.\n');% all files saved

end